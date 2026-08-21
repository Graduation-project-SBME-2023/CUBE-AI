<!--
Copyright (c) MONAI Consortium
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# CUBE.AI Radiology Application

This MONAI Label 0.8.5 application provides interactive and automatic medical
image segmentation for the CUBE.AI backend.

## Supported models

The public model IDs are defined by
`radiology_app/application/registry.py`:

| Model ID | Purpose |
|---|---|
| `deepedit` | Interactive and automatic DeepEdit segmentation |
| `deepgrow_2d` | Slice-based interactive DeepGrow segmentation |
| `deepgrow_3d` | Volumetric interactive DeepGrow segmentation |
| `localization_vertebra` | Vertebra localization |
| `nninteractive` | nnInteractive segmentation |
| `sam` | SAM-based interactive segmentation |
| `segmentation_spleen` | Automatic spleen segmentation |
| `vista` | VISTA3D point-prompt segmentation |
| `voxtell` | Text-prompted VoxTell segmentation |

`generic_segmentation` is a dynamic nnUNet configuration. It is created by the
`POST /create-model` workflow and is therefore intentionally excluded from
normal startup, including `models=all`.

## Starting the app

```bash
monailabel start_server \
  --app Backend/apps/radiology \
  --studies /path/to/images \
  --conf models segmentation_spleen
```

Multiple models can be selected with a comma-separated value:

```bash
monailabel start_server \
  --app Backend/apps/radiology \
  --studies /path/to/images \
  --conf models deepgrow_2d,deepgrow_3d,sam
```

Use `--conf models all` to load every static model. Optional MONAI bundles are
still supported through `--conf bundles <bundle-names>`.

For local train/infer diagnostics without starting the server, use:

```bash
python Backend/apps/radiology/scripts/run_local.py \
  --studies /path/to/images \
  --model segmentation_spleen \
  --test infer
```

## Architecture

- `main.py` is only the compatibility shim required by the MONAI Label loader.
- `radiology_app/application/` owns configuration, model registration, and
  MONAI lifecycle hooks.
- `radiology_app/workflows/` validates and atomically persists dynamic models.
- `radiology_app/datastore/` owns DICOMweb construction, export repair, and
  bounded metadata caches.
- `radiology_app/integrations/` owns third-party integration utilities.
- `lib/` remains the MONAI model plug-in layer containing configs, inferers,
  trainers, transforms, and active-learning tasks.
- `vista3d/configs/` contains the VISTA bundle configuration copied into the
  runtime model directory during startup.
- `pydicom_seg/` contains patched package modules copied into backend images.
- `scripts/` contains executable maintenance and local-development commands.

`lib/trainers/CustomNnUnetTrainer.py` is mounted into the nnUNet package by
Docker Compose so nnUNet can discover the custom trainer by class name.

Dynamic workflow definitions are stored under
`$RADIOLOGY_STATE_DIR/workflows.json`, defaulting to
`$BACKEND_STORAGE_ROOT/state/radiology/workflows.json`. Existing
`workflows_data.json` files are migrated on first successful load and retained
as rollback copies.

## VoxTell cache

Container startup invokes `scripts/build_voxtell_cache.py` only when the cache
file is absent. The script performs a second completeness check before
importing Torch or Transformers, and writes updates atomically while holding an
inter-process lock.
