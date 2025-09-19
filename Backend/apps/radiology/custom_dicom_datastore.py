
import os
import logging
from monailabel.datastore.dicom import DICOMWebDatastore
from monailabel.datastore import dicom
from dicomweb_client import DICOMwebClient

logger = logging.getLogger(__name__)

class MyDICOMWebDatastore(DICOMWebDatastore):
    def get_label_dir_dcm(self, image_id: str) -> str:
        logger.info(f"jjjjjj Image ID: {image_id}")
        image_dir = os.path.realpath(os.path.join(self._datastore.image_path(), image_id))
        logger.info(f"newwww Image Dir (cache): {image_dir}")

        if not os.path.exists(image_dir) or not os.listdir(image_dir):
            dicom.dicom_web_download_series(None, image_id, image_dir, self._client, self._fetch_by_frame)

        if not self._convert_to_nifti:
            return image_dir

        image_nii_gz = os.path.realpath(os.path.join(self._datastore.image_path(), f"{image_id}.nii.gz"))
        return image_nii_gz

