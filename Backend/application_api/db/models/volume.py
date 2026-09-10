from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from application_api.db.base import Base

class Volume(Base):
    __tablename__ = 'volume_series'

    volume_series_instance_uid = Column(String, primary_key=True, index=True)
    orthanc_series_id = Column(String, unique=True, index=True, nullable=False)
    body_part_examined = Column(String, nullable=True)

    study_uid = Column(String, ForeignKey('studies.study_uid'), index=True)
    series_description = Column(String, nullable=True)
    modality = Column(String, nullable=True)
    num_slices = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())  # ISO format date-time
    