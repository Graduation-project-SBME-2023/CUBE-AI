import os
import logging
from abc import abstractmethod
from monailabel.datastore import dicom
from monailabel.interfaces.datastore import Datastore  # patch base interface

logger = logging.getLogger(__name__)

# 1. Patch abstract base class to declare the method
def get_label_dir_dcm_abstract(self, image_id: str) -> str:
    pass

Datastore.get_label_dir_dcm = abstractmethod(get_label_dir_dcm_abstract)

# 2. Provide  concrete implementation patch for DICOMWebDatastore
def get_label_dir_dcm(self, image_id: str) -> str:
    logger.info(f"ohhhhhh Image ID: {image_id}")
    image_dir = os.path.realpath(os.path.join(self._datastore.image_path(), image_id))
    logger.info(f"Image Dir (cache): {image_dir}")

    if not os.path.exists(image_dir) or not os.listdir(image_dir):
        dicom.dicom_web_download_series(None, image_id, image_dir, self._client, self._fetch_by_frame)

    if not self._convert_to_nifti:
        return image_dir

    image_nii_gz = os.path.realpath(os.path.join(self._datastore.image_path(), f"{image_id}.nii.gz"))
    return image_nii_gz

dicom.DICOMWebDatastore.get_label_dir_dcm = get_label_dir_dcm
