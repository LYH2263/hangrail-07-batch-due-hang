import os

# 必须在导入 app.* 之前：app.database 会在模块导入时按 DATABASE_URL 建引擎
os.environ.setdefault("DATABASE_URL", "sqlite://")

from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.router import api_router  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.models.models import HangRail, RailPlacement, Store, WorkOrder  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture()
def make_world(db_session):
    now = datetime(2026, 9, 23, 12, 0, 0)
    store = Store(name="测试店")
    db_session.add(store)
    db_session.flush()

    def _make(
        rail_lengths: list[float],
        orders: list[dict],
        placements: list[dict] | None = None,
    ):
        rails = []
        for i, length in enumerate(rail_lengths):
            rail = HangRail(store_id=store.id, label=f"R{i + 1}杆", length_cm=length)
            db_session.add(rail)
            rails.append(rail)
        db_session.flush()
        wos = []
        for o in orders:
            wo = WorkOrder(
                store_id=store.id,
                ticket_code=o["ticket"],
                garment_name=o.get("garment", "测试衣物"),
                length_cm=o["length"],
                status=o.get("status", "ready"),
                due_at=now + timedelta(hours=o.get("due_hours", 0)),
            )
            db_session.add(wo)
            wos.append(wo)
        db_session.flush()
        for p in placements or []:
            db_session.add(
                RailPlacement(
                    rail_id=rails[p["rail_idx"]].id,
                    order_id=wos[p["order_idx"]].id,
                    start_cm=p["start"],
                    end_cm=p["end"],
                )
            )
        db_session.commit()
        return store, rails, wos

    return _make
