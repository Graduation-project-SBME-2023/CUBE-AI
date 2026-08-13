from __future__ import annotations

import logging

from dicomweb_client import DICOMwebClient
from dicomweb_client.ext.gcp.session_utils import create_session_from_gcp_credentials
from dicomweb_client.session_utils import create_session_from_user_pass
from monailabel.config import settings
from monailabel.datastore.dicom import DICOMwebClientX

from radiology_app.datastore.dicomweb import RadiologyDICOMWebDatastore

logger = logging.getLogger(__name__)


def create_dicomweb_datastore(app) -> RadiologyDICOMWebDatastore:
    """Build the custom datastore using MONAI Label 0.8.5's settings contract."""
    studies = app.studies
    logger.info("Using DICOM WEB: %s", studies)
    session = None
    if "googleapis.com" in studies:
        logger.info("Creating DICOM credentials for Google Cloud")
        session = create_session_from_gcp_credentials()
        client = DICOMwebClient(url=studies, session=session)
    else:
        if (
            settings.MONAI_LABEL_DICOMWEB_USERNAME
            and settings.MONAI_LABEL_DICOMWEB_PASSWORD
        ):
            session = create_session_from_user_pass(
                settings.MONAI_LABEL_DICOMWEB_USERNAME,
                settings.MONAI_LABEL_DICOMWEB_PASSWORD,
            )
        client = DICOMwebClientX(
            url=studies,
            session=session,
            qido_url_prefix=settings.MONAI_LABEL_QIDO_PREFIX,
            wado_url_prefix=settings.MONAI_LABEL_WADO_PREFIX,
            stow_url_prefix=settings.MONAI_LABEL_STOW_PREFIX,
        )

    app._download_dcmqi_tools()
    cache_path = settings.MONAI_LABEL_DICOMWEB_CACHE_PATH
    cache_path = cache_path.strip() if cache_path else ""
    return RadiologyDICOMWebDatastore(
        client=client,
        search_filter=settings.MONAI_LABEL_DICOMWEB_SEARCH_FILTER,
        cache_path=cache_path or None,
        fetch_by_frame=settings.MONAI_LABEL_DICOMWEB_FETCH_BY_FRAME,
        convert_to_nifti=settings.MONAI_LABEL_DICOMWEB_CONVERT_TO_NIFTI,
        cache_ttl=float(settings.MONAI_LABEL_DICOMWEB_CACHE_EXPIRY),
    )
