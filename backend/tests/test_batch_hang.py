from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.models import HangRail, RailPlacement, Store, WorkOrder


def _make_store_with_gap(db, gap_start: float = 50.0, gap_end: float = 70.0, rail_len: float = 100.0):
    """造一根挂杆，中段塞一个既有占位，使两侧各留一段空隙。

    杆长 100，既有占位 [50, 70)：
      - 前段空隙 [0, 50)   —— First-Fit 会优先命中
      - 后段空隙 [70, 100)
    返回 (store_id, rail_id)。
    """
    store = Store(name="测试店")
    db.add(store)
    db.flush()
    rail = HangRail(store_id=store.id, label="T 杆", length_cm=rail_len)
    db.add(rail)
    db.flush()
    blocker = WorkOrder(
        store_id=store.id,
        ticket_code="BLOCKER",
        garment_name="占位衣",
        length_cm=gap_end - gap_start,
        status="hung",
        due_at=datetime.utcnow() + timedelta(days=9),
        hung_at=datetime.utcnow(),
    )
    db.add(blocker)
    db.flush()
    db.add(
        RailPlacement(
            rail_id=rail.id,
            order_id=blocker.id,
            start_cm=gap_start,
            end_cm=gap_end,
        )
    )
    db.commit()
    return store.id, rail.id


def _add_order(db, store_id, ticket, length, due_in_days, status="ready"):
    order = WorkOrder(
        store_id=store_id,
        ticket_code=ticket,
        garment_name=f"衣-{ticket}",
        length_cm=length,
        status=status,
        due_at=datetime.utcnow() + timedelta(days=due_in_days),
    )
    db.add(order)
    db.commit()
    return order


def test_batch_hang_earlier_due_takes_earlier_gap(client, db_session):
    """更早到期的工单即使排在请求末尾提交，也应先占用靠前的空隙。"""
    store_id, rail_id = _make_store_with_gap(db_session)
    # 早到期：50cm，恰好填满前段 [0, 50)
    early = _add_order(db_session, store_id, "EARLY", 50, due_in_days=1)
    # 晚到期：30cm，能进前段 [0,50) 或后段 [70,100)
    late = _add_order(db_session, store_id, "LATE", 30, due_in_days=5)

    # 故意把晚到期的放前面，验证服务端按 due_at 重排
    resp = client.post("/api/hang/batch", json={"order_ids": [late.id, early.id]})
    assert resp.status_code == 200
    body = resp.json()["results"]
    by_id = {r["order_id"]: r for r in body}

    # 两张单都成功
    assert by_id[early.id]["success"] is True
    assert by_id[late.id]["success"] is True

    # 早到期单必须落在前段空隙 [0, 50)
    assert by_id[early.id]["start_cm"] == 0
    assert by_id[early.id]["end_cm"] == 50
    # 晚到期单被挤到后段空隙 [70, 100)
    assert by_id[late.id]["start_cm"] == 70
    assert by_id[late.id]["end_cm"] == 100

    # 占位确实落库，且与结果一一对应
    placements = db_session.scalars(
        select(RailPlacement).where(
            RailPlacement.rail_id == rail_id, RailPlacement.active == 1
        )
    ).all()
    by_order = {p.order_id: (p.start_cm, p.end_cm) for p in placements}
    assert by_order[early.id] == (0.0, 50.0)
    assert by_order[late.id] == (70.0, 100.0)

    db_session.expire_all()
    assert db_session.get(WorkOrder, early.id).status == "hung"
    assert db_session.get(WorkOrder, late.id).status == "hung"


def test_batch_hang_partial_failure_keeps_earlier_placements(client, db_session):
    """后单空间不足时失败，先到期已成功的单不得回滚。"""
    store_id, _rail_id = _make_store_with_gap(db_session)
    # 前段 50、后段 30 的可用空间
    early = _add_order(db_session, store_id, "OK-1", 50, due_in_days=1)
    late = _add_order(db_session, store_id, "TOO-BIG", 60, due_in_days=5)

    resp = client.post("/api/hang/batch", json={"order_ids": [late.id, early.id]})
    assert resp.status_code == 200
    by_id = {r["order_id"]: r for r in resp.json()["results"]}

    assert by_id[early.id]["success"] is True
    assert by_id[early.id]["start_cm"] == 0
    assert by_id[late.id]["success"] is False
    assert by_id[late.id]["reason"] == "挂杆空间不足"

    # 早单占位保留，晚单无任何占位
    assert db_session.scalar(
        select(RailPlacement).where(
            RailPlacement.order_id == early.id, RailPlacement.active == 1
        )
    )
    assert not db_session.scalars(
        select(RailPlacement).where(RailPlacement.order_id == late.id)
    ).all()
    db_session.expire_all()
    assert db_session.get(WorkOrder, early.id).status == "hung"
    assert db_session.get(WorkOrder, late.id).status == "ready"


def test_batch_hang_empty_does_not_write(client, db_session):
    """空选中集合必须失败且不写库。"""
    before = len(db_session.scalars(select(RailPlacement)).all())

    resp = client.post("/api/hang/batch", json={"order_ids": []})
    assert resp.status_code == 400

    after = len(db_session.scalars(select(RailPlacement)).all())
    assert after == before
