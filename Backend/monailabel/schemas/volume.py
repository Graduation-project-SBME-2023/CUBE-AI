from pydantic import BaseModel
from datetime import date, datetime

class VolumeBase(BaseModel):
    volume_series_instance_uid: str
    study_uid: str
    series_description: str | None = None
    modality: str | None = None
    num_slices: int | None = None
    created_at: date | None = None  # ISO format date-time

class VolumeCreate(VolumeBase):
    pass

class VolumeUpdate(BaseModel):
    series_description: str | None = None
    # modality: str | None = None
    # num_slices: int | None = None


class Volume(VolumeBase):
    class Config:
        orm_mode = True