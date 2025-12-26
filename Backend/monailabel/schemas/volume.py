from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional
from pydantic import validator
class VolumeBase(BaseModel):
    volume_series_instance_uid: str
    study_uid: str
    series_description: Optional[str] = None
    modality: Optional[str] = None
    num_slices: Optional[int] = None
    created_at: Optional[date] = None  # ISO format date-time
    @validator("created_at", pre=True)
    def extract_date(cls, v):
        if isinstance(v, datetime):
            return v.date()  # keep only the date part
        return v
class VolumeCreate(VolumeBase):
    pass

class VolumeUpdate(BaseModel):
    series_description: Optional[str] = None
    # modality: str | None = None
    # num_slices: int | None = None


class Volume(VolumeBase):
    orthanc_series_id: str | None = None
    class Config:
        orm_mode = True