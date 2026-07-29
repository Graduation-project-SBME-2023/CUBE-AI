from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from core.database import Base

class Segmentation(Base):
    __tablename__ = 'segmentations'

    label_series_instance_uid = Column(String, nullable=False, primary_key=True)
    orthanc_series_id = Column(String, unique=True, index=True, nullable=False)

    volume_series_instance_uid = Column(String, nullable=False)
    segmentation_type = Column(String, nullable=False)
    # Exact clinical target stored in the DICOM SegmentLabel.  Historical
    # records may be null until Orthanc is re-synchronised.
    target_label = Column(String, nullable=True, index=True)
    method = Column(String, nullable=True)

    name = Column(String, ForeignKey('models.name'), nullable=True)
    created_at = Column(DateTime, nullable=True)
