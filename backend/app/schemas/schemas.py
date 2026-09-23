from datetime import datetime
from pydantic import BaseModel


class StoreOut(BaseModel):
    id: int
    name: str
    model_config = {"from_attributes": True}


class RailOut(BaseModel):
    id: int
    store_id: int
    label: str
    length_cm: float
    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: int
    store_id: int
    ticket_code: str
    garment_name: str
    length_cm: float
    status: str
    due_at: datetime
    hung_at: datetime | None
    model_config = {"from_attributes": True}


class HangRequest(BaseModel):
    order_id: int
    rail_id: int | None = None


class BatchHangRequest(BaseModel):
    order_ids: list[int]
    rail_id: int | None = None


class BatchHangItem(BaseModel):
    order_id: int
    ticket_code: str
    success: bool
    rail_id: int | None = None
    rail_label: str | None = None
    start_cm: float | None = None
    end_cm: float | None = None
    reason: str | None = None


class BatchHangResult(BaseModel):
    results: list[BatchHangItem]


class PickupRequest(BaseModel):
    ticket_code: str


class OccupancySeg(BaseModel):
    order_id: int
    ticket_code: str
    garment_name: str
    start_cm: float
    end_cm: float


class OccupancyOut(BaseModel):
    rail_id: int
    label: str
    length_cm: float
    segments: list[OccupancySeg]
