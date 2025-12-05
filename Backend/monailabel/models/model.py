from sqlalchemy import Column, Integer, String, DateTime, JSON
from core.database import Base

class Model(Base):
    __tablename__ = 'models'

    model_id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    model_name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    description = Column(String, nullable=True)
    configuration = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True)