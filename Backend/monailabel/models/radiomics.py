from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, text,UUID
from core.database import Base

class Radiomics(Base):
    __tablename__ = 'radiomics_features'

    id =  Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    label_series_instance_uid = Column(String, ForeignKey('segmentations.label_series_instance_uid'), index=True)
    features = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=True)  # ISO format date-time