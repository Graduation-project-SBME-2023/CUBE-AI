from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from core.database import Base

class Segmentation(Base):
    __tablename__ = 'segmentations'

    label_series_instance_uid = Column(String, nullable=False, primary_key=True)
    volume_series_instance_uid = Column(String, nullable=False)
    segmentation_type = Column(String, nullable=False)
    method = Column(String, nullable=True)
    model_name = Column(String, ForeignKey('models.model_name'), nullable=True)
    created_at = Column(DateTime, nullable=True)