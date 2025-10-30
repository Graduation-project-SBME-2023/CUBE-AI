from pydantic import BaseModel
from datetime import datetime

class SegmentationBase(BaseModel):
    label_series_instance_uid: str
    volume_series_instance_uid: str
    segmentation_type: str
    method: str | None = None
    model_name: str | None = None
    created_at: datetime | None = None  # ISO format date-time

class SegmentationCreate(SegmentationBase):
    pass

class SegmentationUpdate(BaseModel):
    segmentation_type: str | None = None
    method: str | None = None
    model_name: str | None = None
    created_at: datetime | None = None  # ISO format date-time

class Segmentation(SegmentationBase):
    class Config:
        orm_mode = True