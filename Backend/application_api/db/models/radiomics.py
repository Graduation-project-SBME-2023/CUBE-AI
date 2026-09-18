from sqlalchemy import Column, String, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from application_api.db.base import Base

class Radiomics(Base):
    __tablename__ = 'radiomics_features'

    id =  Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    label_series_instance_uid = Column(String, ForeignKey('segmentations.label_series_instance_uid'), index=True)
    features = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
