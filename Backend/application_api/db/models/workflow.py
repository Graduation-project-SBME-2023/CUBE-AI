from sqlalchemy import Column, Integer, String, DateTime
from application_api.db.base import Base

class Workflow(Base):
    __tablename__ = 'workflow'

    task_name = Column(String, primary_key=True, nullable=False)