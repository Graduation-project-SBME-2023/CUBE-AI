import os
import unittest

import nrrd
import numpy as np
import SimpleITK as sitk
import torch
from monailabel.transform.writer import Writer

from lib.infers.voxtell import ImageProcessor


class VoxTellGeometryTests(unittest.TestCase):
    def test_writer_emits_three_dimensional_physical_geometry(self):
        image = sitk.GetImageFromArray(np.zeros((2, 3, 4), dtype=np.uint8))
        image.SetSpacing((0.7, 0.8, 2.5))
        image.SetOrigin((-120.0, 45.0, 300.0))
        image.SetDirection((1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0))

        prediction_xyz = sitk.GetArrayFromImage(image).transpose(2, 1, 0)
        data = {
            "image_path": "source.nrrd",
            "pred": torch.from_numpy(prediction_xyz.copy()),
            "pred_meta_dict": {
                "affine": torch.as_tensor(
                    ImageProcessor.ras_affine_from_sitk(image),
                    dtype=torch.float64,
                )
            },
            "result_extension": ".nrrd",
            "result_dtype": "uint8",
            "result_compress": False,
        }

        output_path, _ = Writer(label="pred")(data)
        try:
            written, header = nrrd.read(output_path)
        finally:
            os.unlink(output_path)

        self.assertEqual(written.shape, (4, 3, 2))
        self.assertEqual(int(header["dimension"]), 3)
        self.assertEqual(np.asarray(header["space origin"]).shape, (3,))
        self.assertEqual(np.asarray(header["space directions"]).shape, (3, 3))
        self.assertTrue(
            np.allclose(header["space origin"], image.GetOrigin(), atol=1e-6)
        )
        self.assertTrue(
            np.allclose(
                np.linalg.norm(header["space directions"], axis=1),
                image.GetSpacing(),
                atol=1e-6,
            )
        )


if __name__ == "__main__":
    unittest.main()
