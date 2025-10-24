from pydantic import BaseModel

class SegmentationBase(BaseModel):
    label_series_instance_uid: str
    volume_series_instance_uid: str
    segmentation_type: str
    method: str | None = None
    model_name: str | None = None
    created_at: str | None = None  # ISO format date-time

class SegmentationCreate(SegmentationBase):
    pass

class Segmentation(SegmentationBase):
    class Config:
        orm_mode = True