from sqlalchemy import Column, Integer, String, DateTime, JSON, text, UUID
from application_api.db.base import Base

class Model(Base):
    __tablename__ = 'models'

    model_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    name = Column(String, nullable=False)

    # automatic / interactive
    type = Column(String, nullable=False)

    modality = Column(String, nullable=False)  # "CT", "MRI", "any"

    anatomy = Column(JSON, nullable=True)  # ["spleen", "liver"]

    # performance per label (Dice, etc.)
    labels = Column(JSON, nullable=True)

    # so it contain any parameters needed for the model execution
    params_schema = Column(JSON, nullable=True)

    description = Column(String, nullable=True)

    config = Column(JSON, nullable=True)

    dimension = Column(Integer, nullable=True)
    

    created_at = Column(DateTime, nullable=True)
