# Backend runtime storage

`Backend/storage/` is the single boundary for generated backend data. This
README is the only file below this directory that is tracked by Git; all other
contents are created by services or by the storage migration.

```text
Backend/storage/
|-- README.md                         # the only tracked file here
|-- hypotheses/
|   `-- workspace_<session-id>/
|       |-- state/                    # state/config/review JSON files
|       |-- data/
|       |   |-- populations/          # cohort labels, targets, clinical data
|       |   |-- volumes/              # source NIfTI volumes
|       |   `-- masks/                # segmentation masks
|       |-- outputs/
|       |   |-- agents/               # structured agent outputs
|       |   |-- overlays/             # preview GIFs
|       |   |-- features/             # CSV/YAML/logs/plots
|       |   |-- modeling/             # trained models and modeling logs
|       |   `-- reports/              # PDF and LaTeX reports
|       `-- temp/
|           |-- literature/           # scratch literature-review output
|           `-- segmentations/
|-- data/
|   |-- medical/{images,labels,exports}/
|   `-- nnunet/{nnUNet_raw,nnUNet_preprocessed,nnUNet_results,mappings}/
|-- models/                           # weights, embeddings, and runtime configs
|-- jobs/inference/                   # job metadata and results
|-- state/radiology/                  # persistent dynamic-model definitions
|-- cache/{huggingface,monailabel,literature}/
|-- logs/{agent-system,radiology,nginx}/
|-- temp/{radiology,vista,latex}/
`-- _migrated-legacy/<origin>/        # conflicts and ownerless legacy files
```

## Lifecycle

- Durable: `hypotheses/*/{state,data,outputs}`, `data/`, `models/`, `jobs/`,
  and `state/`.
- Disposable: global `cache/` and `temp/`, plus each workspace's `temp/`
  directories. Literature knowledge cache files are shared by all hypotheses in
  `cache/literature/`; workspaces must not create their own literature cache.
- `_migrated-legacy/` preserves conflicts and ownerless files. Review it before
  deleting any migration source or rollback data.

Deleting a workspace removes only that session. Never delete `state/`, `data/`,
or `outputs/` as cache cleanup.

## Environment contract

Set `BACKEND_STORAGE_ROOT` to this directory (`./storage` for a local Backend
process and `/storage` in containers). Derived locations are defined once in
`Backend/storage_paths.py`. The compatibility variables
`AGENTS_WORKSPACE_DIR`, `GENERAL_DATA_DIR`, `NNUNET_DIR`, `MODELS_DIR`, and
`INFERENCE_JOB_DIR` may override individual deployment paths. Runtime logs and
temporary files may be redirected with `BACKEND_LOG_DIR` and
`BACKEND_TEMP_DIR`. Dynamic radiology workflow state may be redirected with
`RADIOLOGY_STATE_DIR`.
