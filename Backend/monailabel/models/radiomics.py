from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON
from core.database import Base

class Radiomics(Base):
    __tablename__ = 'radiomics_features'

    id =  Column(Integer, primary_key=True, index=True, autoincrement=True)

    label_series_instance_uid = Column(String, ForeignKey('segmentations.label_series_instance_uid'), index=True)
    features = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=True)  # ISO format date-time