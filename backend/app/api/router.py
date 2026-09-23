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


def _candidate_rails(db: Session, order: WorkOrder, rail_id: int | None) -> list[HangRail]:
    rail_q = select(HangRail).where(HangRail.store_id == order.store_id)
    if rail_id:
        rail_q = rail_q.where(HangRail.id == rail_id)
    return list(db.scalars(rail_q.order_by(HangRail.id)).all())


def _active_segments(db: Session, rail_id: int) -> list[Segment]:
    active = db.scalars(
        select(RailPlacement).where(RailPlacement.rail_id == rail_id, RailPlacement.active == 1)
    ).all()
    return [Segment(p.start_cm, p.end_cm) for p in active]


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
    rails = _candidate_rails(db, order, body.rail_id)
    if not rails:
        raise HTTPException(404, "无可用挂杆")

    for rail in rails:
        place = first_fit(rail.length_cm, _active_segments(db, rail.id), order.length_cm)
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
    """按到期先后批量上杆：due_at 早的先做 First-Fit，部分失败不回滚已占位置。"""
    if not body.order_ids:
        raise HTTPException(400, "批量上杆不能为空")

    # 去重，每个工单只处理一次；不存在的 id 记为失败。
    unique_ids = list(dict.fromkeys(body.order_ids))
    orders: list[WorkOrder] = []
    missing: list[int] = []
    for oid in unique_ids:
        order = db.get(WorkOrder, oid)
        if order:
            orders.append(order)
        else:
            missing.append(oid)

    hangable = [o for o in orders if o.status in ("ready", "overdue")]
    non_hangable = [o for o in orders if o.status not in ("ready", "overdue")]
    # 到期早的优先，平局按 id 保证确定性
    hangable.sort(key=lambda o: (o.due_at, o.id))

    # rail_id -> 该杆当前已占段（含本批已成功的占位），先到期者先占空隙
    occupied: dict[int, list[Segment]] = {}

    results: list[BatchHangItem] = []
    for order in hangable:
        rails = _candidate_rails(db, order, body.rail_id)
        placed = False
        for rail in rails:
            if rail.id not in occupied:
                occupied[rail.id] = _active_segments(db, rail.id)
            place = first_fit(rail.length_cm, occupied[rail.id], order.length_cm)
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
            occupied[rail.id].append(Segment(place.start_cm, place.end_cm))
            order.status = "hung"
            order.hung_at = datetime.utcnow()
            results.append(
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
            results.append(
                BatchHangItem(
                    order_id=order.id,
                    ticket_code=order.ticket_code,
                    success=False,
                    reason="无可用挂杆" if not rails else "挂杆空间不足",
                )
            )

    for order in non_hangable:
        results.append(
            BatchHangItem(
                order_id=order.id,
                ticket_code=order.ticket_code,
                success=False,
                reason="工单状态不可上杆",
            )
        )
    for oid in missing:
        results.append(
            BatchHangItem(order_id=oid, ticket_code="", success=False, reason="工单不存在")
        )

    # 一次性提交：后单失败不回滚先成功的占位
    db.commit()
    return BatchHangResult(results=results)


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
