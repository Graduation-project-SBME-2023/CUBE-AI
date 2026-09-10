from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from application_api.db.base import Base
from .chat_link_study import chat_study_association
from sqlalchemy.dialects.postgresql import UUID
class Study(Base):
    __tablename__ = 'studies'

    study_uid = Column(String, primary_key=True, index=True)
    orthanc_study_id = Column(String, unique=True, index=True, nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey('patients.patient_id'), index=True)
    description = Column(String, nullable=True)
    modality = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    chat_sessions = relationship(
        "ChatSession",
        secondary=chat_study_association,
        back_populates="studies",
    )
