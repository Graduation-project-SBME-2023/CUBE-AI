# Runtime package overrides

These modules replace files inside third-party Python packages used by the
backend containers. They are deployment inputs, not importable application
modules.

- `pydicom_seg/` contains the patched reader and segmentation dataset modules
  copied by the backend Dockerfiles.
- `nnunetv2/` contains the custom trainer mounted by Docker Compose.

Keep the target package paths in `deploy/` synchronized when adding or moving
an override.
