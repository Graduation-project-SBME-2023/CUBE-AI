"""Ingestion of volumetric images that arrive outside DICOM.

A NIfTI/NRRD/MetaImage volume is converted into a real DICOM series and stored
in Orthanc, so that everything downstream -- segmentation, DICOM SEG creation,
radiomics, OHIF -- treats it exactly like a scan that came from a modality.
"""
