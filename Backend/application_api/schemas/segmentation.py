from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class SegmentationBase(BaseModel):
    label_series_instance_uid: str
    volume_series_instance_uid: str
    segmentation_type: Optional[str] = None
    target_label: Optional[str] = None
    method: Optional[str] = None
    name:  Optional[str] = None
    created_at: Optional[datetime] = None  # ISO format date-time

class SegmentationCreate(SegmentationBase):
    pass

class SegmentationUpdate(BaseModel):
    segmentation_type: Optional[str] = None
    target_label: Optional[str] = None
    method: Optional[str] = None
    name:  Optional[str] = None
    created_at: Optional[datetime] = None # ISO format date-time

class Segmentation(SegmentationBase):
    orthanc_series_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
