from sqlalchemy import func, select

from app.models.models import RailPlacement, WorkOrder


def _items_by_ticket(payload):
    return {it["ticket_code"]: it for it in payload["items"]}


def test_batch_earlier_due_takes_leftmost_gap(client, make_world, db_session):
    """提交顺序与到期顺序相反时，仍应是更早到期的工单占用更靠前的空隙。"""
    _store, rails, wos = make_world(
        rail_lengths=[100],
        orders=[
            {"ticket": "LATE", "length": 30, "due_hours": 48},
            {"ticket": "EARLY", "length": 30, "due_hours": 1},
        ],
        placements=[{"rail_idx": 0, "order_idx": 0, "start": 40, "end": 60}],
    )
    # 预置占位用 LATE 工单（40-60），使其状态改为 hung，避免它本身还在 ready
    late, early = wos
    late.status = "hung"
    db_session.commit()

    # 故意把到期更晚的工单排在提交列表前面
    resp = client.post("/api/hang/batch", json={"order_ids": [late.id, early.id]})
    assert resp.status_code == 200, resp.text
    by_ticket = _items_by_ticket(resp.json())
    assert by_ticket["EARLY"]["success"] is True
    assert by_ticket["LATE"]["success"] is False  # 已 hung，不可重复上杆
    assert by_ticket["EARLY"]["start_cm"] == 0
    assert by_ticket["EARLY"]["end_cm"] == 30

    placements = db_session.scalars(
        select(RailPlacement).where(RailPlacement.order_id == early.id)
    ).all()
    assert len(placements) == 1
    assert (placements[0].start_cm, placements[0].end_cm) == (0, 30)


def test_batch_due_order_decides_gap_allocation(client, make_world, db_session):
    """两张可上杆工单、左右两个等宽空隙：早到期的占左空隙，晚到期的只能进右空隙。"""
    _store, rails, wos = make_world(
        rail_lengths=[100],
        orders=[
            {"ticket": "OCC", "length": 20, "status": "hung", "due_hours": 0},
            {"ticket": "LATE", "length": 30, "due_hours": 48},
            {"ticket": "EARLY", "length": 30, "due_hours": 1},
        ],
        placements=[{"rail_idx": 0, "order_idx": 0, "start": 40, "end": 60}],
    )
    late, early = wos[1], wos[2]

    resp = client.post("/api/hang/batch", json={"order_ids": [late.id, early.id]})
    assert resp.status_code == 200, resp.text
    by_ticket = _items_by_ticket(resp.json())
    assert by_ticket["EARLY"]["start_cm"] == 0
    assert by_ticket["EARLY"]["end_cm"] == 30
    # 左空隙被早到期工单占走后，只剩 10cm，晚到期工单落到 60 起的右空隙
    assert by_ticket["LATE"]["start_cm"] == 60
    assert by_ticket["LATE"]["end_cm"] == 90
    assert resp.json()["succeeded"] == 2


def test_batch_empty_does_not_write(client, make_world, db_session):
    """空选中集合必须失败，且不写入任何占位。"""
    make_world(
        rail_lengths=[100],
        orders=[{"ticket": "A", "length": 30, "due_hours": 1}],
    )
    before = db_session.scalar(select(func.count()).select_from(RailPlacement))

    resp = client.post("/api/hang/batch", json={"order_ids": []})
    assert resp.status_code == 400

    after = db_session.scalar(select(func.count()).select_from(RailPlacement))
    assert before == after == 0
    only = db_session.scalar(select(WorkOrder))
    assert only.status == "ready"
    assert only.hung_at is None


def test_batch_partial_success_is_not_rolled_back(client, make_world, db_session):
    """后单因空间失败，不得回滚先成功工单的占位。"""
    _store, rails, wos = make_world(
        rail_lengths=[50],
        orders=[
            {"ticket": "FITS", "length": 20, "due_hours": 1},
            {"ticket": "TOO-BIG", "length": 60, "due_hours": 48},
        ],
    )
    fits, too_big = wos

    resp = client.post(
        "/api/hang/batch", json={"order_ids": [too_big.id, fits.id]}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["succeeded"] == 1
    assert data["failed"] == 1
    by_ticket = _items_by_ticket(data)
    assert by_ticket["FITS"]["success"] is True
    assert by_ticket["FITS"]["start_cm"] == 0
    assert by_ticket["TOO-BIG"]["success"] is False
    assert by_ticket["TOO-BIG"]["reason"] == "挂杆空间不足"

    db_session.expire_all()
    placements = db_session.scalars(select(RailPlacement)).all()
    assert len(placements) == 1
    assert (placements[0].start_cm, placements[0].end_cm) == (0, 20)
    assert db_session.get(WorkOrder, fits.id).status == "hung"
    assert db_session.get(WorkOrder, too_big.id).status == "ready"


def test_batch_unknown_order_rejected_but_others_proceed(client, make_world):
    _store, _rails, wos = make_world(
        rail_lengths=[50],
        orders=[{"ticket": "OK", "length": 20, "due_hours": 1}],
    )
    resp = client.post(
        "/api/hang/batch", json={"order_ids": [99999, wos[0].id]}
    )
    assert resp.status_code == 200, resp.text
    by_ticket = _items_by_ticket(resp.json())
    assert by_ticket[""]["success"] is False
    assert by_ticket[""]["reason"] == "工单不存在"
    assert by_ticket["OK"]["success"] is True


def test_single_hang_endpoint_still_works(client, make_world):
    """单件上杆入口保留。"""
    _store, rails, wos = make_world(
        rail_lengths=[50],
        orders=[{"ticket": "ONE", "length": 20, "due_hours": 1}],
    )
    resp = client.post("/api/hang", json={"order_id": wos[0].id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ticket_code"] == "ONE"
    assert body["status"] == "hung"
