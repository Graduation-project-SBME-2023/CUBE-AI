from sqlalchemy import Column, Integer, String, DateTime, JSON, text,UUID
from core.database import Base

class Model(Base):
    __tablename__ = 'models'

    model_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    dimension = Column(Integer, nullable=True)
    labels = Column(JSON, nullable=True)
    description = Column(String, nullable=True)
    config = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True)