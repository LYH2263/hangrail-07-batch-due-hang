from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import HangRail, RailPlacement, Store, WorkOrder
from app.schemas.schemas import (
    BatchHangItem,
    BatchHangRequest,
    BatchHangResult,
    HangRequest,
    OccupancyOut,
    OccupancySeg,
    OrderOut,
    PickupRequest,
    RailOut,
    StoreOut,
)
from app.services.rail_engine import Segment, first_fit

api_router = APIRouter()


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/stores", response_model=list[StoreOut])
def stores(db: Session = Depends(get_db)):
    return db.scalars(select(Store).order_by(Store.id)).all()


@api_router.get("/rails", response_model=list[RailOut])
def rails(db: Session = Depends(get_db)):
    return db.scalars(select(HangRail).order_by(HangRail.id)).all()


@api_router.get("/orders", response_model=list[OrderOut])
def orders(db: Session = Depends(get_db)):
    return db.scalars(select(WorkOrder).order_by(WorkOrder.id.desc())).all()


@api_router.get("/occupancy/{rail_id}", response_model=OccupancyOut)
def occupancy(rail_id: int, db: Session = Depends(get_db)):
    rail = db.get(HangRail, rail_id)
    if not rail:
        raise HTTPException(404, "挂杆不存在")
    placements = db.scalars(
        select(RailPlacement).where(RailPlacement.rail_id == rail_id, RailPlacement.active == 1)
    ).all()
    segs = []
    for p in placements:
        order = db.get(WorkOrder, p.order_id)
        if not order:
            continue
        segs.append(
            OccupancySeg(
                order_id=order.id,
                ticket_code=order.ticket_code,
                garment_name=order.garment_name,
                start_cm=p.start_cm,
                end_cm=p.end_cm,
            )
        )
    segs.sort(key=lambda s: s.start_cm)
    return OccupancyOut(rail_id=rail.id, label=rail.label, length_cm=rail.length_cm, segments=segs)


@api_router.post("/hang", response_model=OrderOut)
def hang(body: HangRequest, db: Session = Depends(get_db)):
    order = db.get(WorkOrder, body.order_id)
    if not order:
        raise HTTPException(404, "工单不存在")
    if order.status not in ("ready", "overdue"):
        raise HTTPException(400, "工单状态不可上杆")
    rail_q = select(HangRail).where(HangRail.store_id == order.store_id)
    if body.rail_id:
        rail_q = rail_q.where(HangRail.id == body.rail_id)
    rails = db.scalars(rail_q.order_by(HangRail.id)).all()
    if not rails:
        raise HTTPException(404, "无可用挂杆")

    for rail in rails:
        active = db.scalars(
            select(RailPlacement).where(RailPlacement.rail_id == rail.id, RailPlacement.active == 1)
        ).all()
        occupied = [Segment(p.start_cm, p.end_cm) for p in active]
        place = first_fit(rail.length_cm, occupied, order.length_cm)
        if place is None:
            continue
        db.add(
            RailPlacement(
                rail_id=rail.id,
                order_id=order.id,
                start_cm=place.start_cm,
                end_cm=place.end_cm,
            )
        )
        order.status = "hung"
        order.hung_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
        return order

    raise HTTPException(409, "挂杆空间不足")


@api_router.post("/hang/batch", response_model=BatchHangResult)
def hang_batch(body: BatchHangRequest, db: Session = Depends(get_db)):
    # 空集合直接失败，且在任何写操作之前返回
    if not body.order_ids:
        raise HTTPException(400, "未选择工单")

    unique_ids = list(dict.fromkeys(body.order_ids))
    orders = [db.get(WorkOrder, oid) for oid in unique_ids]

    items: list[BatchHangItem] = []
    eligible: list[WorkOrder] = []
    for oid, order in zip(unique_ids, orders):
        if order is None:
            items.append(
                BatchHangItem(order_id=oid, ticket_code="", success=False, reason="工单不存在")
            )
            continue
        if order.status not in ("ready", "overdue"):
            items.append(
                BatchHangItem(
                    order_id=order.id,
                    ticket_code=order.ticket_code,
                    success=False,
                    reason="工单状态不可上杆",
                )
            )
            continue
        eligible.append(order)

    # 到期早的先套现网：按 due_at 升序逐个 First-Fit
    eligible.sort(key=lambda o: (o.due_at, o.id))

    # 本批已成功的占位也要计入占用，保证“先到期先占空隙”
    occupied_cache: dict[int, list[Segment]] = {}
    rails_cache: dict[int, list[HangRail]] = {}
    now = datetime.utcnow()

    def _rails_for(store_id: int) -> list[HangRail]:
        if store_id not in rails_cache:
            rail_q = select(HangRail).where(HangRail.store_id == store_id)
            if body.rail_id:
                rail_q = rail_q.where(HangRail.id == body.rail_id)
            rails_cache[store_id] = list(db.scalars(rail_q.order_by(HangRail.id)).all())
        return rails_cache[store_id]

    def _occupied(rail_id: int) -> list[Segment]:
        if rail_id not in occupied_cache:
            active = db.scalars(
                select(RailPlacement).where(
                    RailPlacement.rail_id == rail_id, RailPlacement.active == 1
                )
            ).all()
            occupied_cache[rail_id] = [Segment(p.start_cm, p.end_cm) for p in active]
        return occupied_cache[rail_id]

    for order in eligible:
        rails = _rails_for(order.store_id)
        if not rails:
            items.append(
                BatchHangItem(
                    order_id=order.id,
                    ticket_code=order.ticket_code,
                    success=False,
                    reason="无可用挂杆",
                )
            )
            continue

        placed = False
        for rail in rails:
            place = first_fit(rail.length_cm, _occupied(rail.id), order.length_cm)
            if place is None:
                continue
            db.add(
                RailPlacement(
                    rail_id=rail.id,
                    order_id=order.id,
                    start_cm=place.start_cm,
                    end_cm=place.end_cm,
                )
            )
            # 同事务内立即让后续工单看到新占位（不提交）
            db.flush()
            _occupied(rail.id).append(Segment(place.start_cm, place.end_cm))
            order.status = "hung"
            order.hung_at = now
            items.append(
                BatchHangItem(
                    order_id=order.id,
                    ticket_code=order.ticket_code,
                    success=True,
                    rail_id=rail.id,
                    rail_label=rail.label,
                    start_cm=place.start_cm,
                    end_cm=place.end_cm,
                )
            )
            placed = True
            break

        if not placed:
            items.append(
                BatchHangItem(
                    order_id=order.id,
                    ticket_code=order.ticket_code,
                    success=False,
                    reason="挂杆空间不足",
                )
            )

    # 一次性提交：部分成功时仅落库成功的占位，不因后单失败回滚先成功的工单
    if any(it.success for it in items):
        db.commit()

    succeeded = sum(1 for it in items if it.success)
    # 结果按票号（工单）稳定排列，便于和提交顺序对照
    items.sort(key=lambda it: unique_ids.index(it.order_id))
    return BatchHangResult(
        requested=len(unique_ids),
        succeeded=succeeded,
        failed=len(items) - succeeded,
        items=items,
    )


@api_router.post("/pickup", response_model=OrderOut)
def pickup(body: PickupRequest, db: Session = Depends(get_db)):
    order = db.scalar(select(WorkOrder).where(WorkOrder.ticket_code == body.ticket_code))
    if not order:
        raise HTTPException(404, "取件码无效")
    if order.status != "hung":
        raise HTTPException(400, "工单未在挂杆上")
    placements = db.scalars(
        select(RailPlacement).where(RailPlacement.order_id == order.id, RailPlacement.active == 1)
    ).all()
    for p in placements:
        p.active = 0
    order.status = "picked"
    db.commit()
    db.refresh(order)
    return order


@api_router.post("/overdue/scan", response_model=list[OrderOut])
def overdue_scan(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    hung = db.scalars(select(WorkOrder).where(WorkOrder.status == "hung")).all()
    marked = []
    for o in hung:
        if o.due_at < now:
            o.status = "overdue"
            marked.append(o)
    ready = db.scalars(select(WorkOrder).where(WorkOrder.status == "ready")).all()
    for o in ready:
        if o.due_at < now:
            o.status = "overdue"
            marked.append(o)
    db.commit()
    return marked


@api_router.get("/overdue", response_model=list[OrderOut])
def overdue_list(db: Session = Depends(get_db)):
    return db.scalars(select(WorkOrder).where(WorkOrder.status == "overdue").order_by(WorkOrder.due_at)).all()
