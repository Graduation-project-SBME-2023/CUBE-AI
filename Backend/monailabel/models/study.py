from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from core.database import Base
from .chat_link_study import chat_study_association
from sqlalchemy.dialects.postgresql import UUID
class Study(Base):
    __tablename__ = 'studies'

    study_uid = Column(String, primary_key=True, index=True)
    patient_id = Column(UUID(as_uuid=True), ForeignKey('patients.patient_id'), index=True)
    description = Column(String, nullable=True)
    modality = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=True)

    chat_sessions = relationship(
        "ChatSession",  # ← string reference
        secondary=chat_study_association,
        back_populates="studies",
    )
