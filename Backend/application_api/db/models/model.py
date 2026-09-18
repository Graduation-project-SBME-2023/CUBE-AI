from sqlalchemy import Column, Integer, String, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from application_api.db.base import Base

class Model(Base):
    __tablename__ = 'models'

    model_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    name = Column(String, nullable=False, unique=True)

    # automatic / interactive
    type = Column(String, nullable=False)

    modality = Column(String, nullable=False)  # "CT", "MRI", "any"

    anatomy = Column(JSONB, nullable=True)  # ["spleen", "liver"]

    # performance per label (Dice, etc.)
    labels = Column(JSONB, nullable=True)

    # so it contain any parameters needed for the model execution
    params_schema = Column(JSONB, nullable=True)

    description = Column(String, nullable=True)

    config = Column(JSONB, nullable=True)

    dimension = Column(Integer, nullable=True)


    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # (closed 2026-09-15): kept — the MONAI /info/ sync writer mutates
    # this row on every sync. removed current_version/weights_digest/
    # weights_registered_at/status: /info/ supplies nothing to put in them.
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
