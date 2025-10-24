from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from core.database import Base

class Volume(Base):
    __tablename__ = 'volume_series'

    volume_series_instance_uid = Column(String, primary_key=True, index=True)
    study_uid = Column(String, ForeignKey('studies.study_uid'), index=True)
    series_description = Column(String, nullable=True)
    modality = Column(String, nullable=True)
    num_slices = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=True)  # ISO format date-time
    