from sqlalchemy import Column, Integer, String, DateTime, JSON, text,UUID
from core.database import Base

class Model(Base):
    __tablename__ = 'models'

    model_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    model_name = Column(String, nullable=False)
    types = Column(JSON, nullable=False, default=[])  # <-- multiple types
    description = Column(String, nullable=True)
    configuration = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True)