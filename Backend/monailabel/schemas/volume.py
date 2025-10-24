from pydantic import BaseModel

class VolumeBase(BaseModel):
    volume_series_instance_uid: str
    study_uid: str
    series_description: str | None = None
    modality: str | None = None
    num_slices: int | None = None
    created_at: str | None = None  # ISO format date-time

class VolumeCreate(VolumeBase):
    pass

class Volume(VolumeBase):
    class Config:
        orm_mode = True