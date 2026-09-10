# 🗄️ Database Documentation

CUBE.AI uses **PostgreSQL 18** as its primary database, managed via async **SQLAlchemy** ORM and
**Alembic** for schema migrations. All database access — models, repositories, schemas, seeders,
and migrations — lives under `Backend/application_api/`; per the service-ownership boundary in the
root `CLAUDE.md`, no other service (MONAI Label, the agent system) ever receives `DATABASE_URL` or
imports SQLAlchemy.

> This page previously described tables (`list_workflow`, `research_sessions`, `session_studies`,
> `agent_outputs`) that do not exist in any model or migration, and omitted the tables that actually
> hold most of the application's data. See `cubeai-docs/backend/database_audit.md` (finding D-34) for
> the audit that caught the drift; this rewrite reflects the schema as of that audit.

---

## Connection Details

| Setting | Value |
|---|---|
| Container name | `postgres-backend` (Docker) |
| Host (from other containers) | `postgres` |
| Host (from the developer machine) | `localhost` |
| Port (container-internal) | `5432` |
| Port (published to the host) | `5433` |
| Database | `workflow_db` |
| User | `workflow_user` |
| Password | Defined in `deploy/.env` — copy `deploy/.env.example` and set your own; never commit real values |

**Connection string (async, used by the app and by Alembic via a driver rewrite):**
```
postgresql+asyncpg://workflow_user:<password>@postgres:5432/workflow_db
```

---

## Schema Overview

Tables fall into two groups: the **Orthanc mirror** (kept in sync with the PACS by
`application_api/repositories/orthanc_sync.py`) and the **application data** the agent pipeline and
chat UI write directly.

### Orthanc mirror

| Table | Model | Purpose |
|---|---|---|
| `patients` | `db/models/patient.py` | One row per Orthanc patient (`orthanc_patient_id`), plus name/DOB/gender |
| `studies` | `db/models/study.py` | One row per Orthanc study (`orthanc_study_id`), FK to `patients` |
| `volume_series` | `db/models/volume.py` | One row per non-SEG Orthanc series (`orthanc_series_id`), FK to `studies` |
| `segmentations` | `db/models/segmentation.py` | One row per DICOM-SEG series (`orthanc_series_id`), FK to `volume_series` |

These four tables are **not** the source of truth for imaging — Orthanc is. They exist so the
application API can query metadata (patient name, series description, segmentation target) without
hitting Orthanc's REST API on every request. Rows are created by the Orthanc Lua webhook
(`Backend/orthanc/lua/on-stable-series.lua` → `POST /sync/orthanc-webhook`) and by a periodic
reconciliation pass (`sync_all_from_orthanc`, run on a timer from the FastAPI lifespan in `main.py`).
See `cubeai-docs/backend/database_audit.md` for the consistency model and its known gaps.

### Application data

| Table | Model | Purpose |
|---|---|---|
| `models` | `db/models/model.py` | Registered segmentation models (seeded + synced from MONAI Label's `/info/`) |
| `radiomics_features` | `db/models/radiomics.py` | Extracted radiomics features per segmentation |
| `chat_sessions` | `db/models/chat_sessions.py` | Research Playground chat sessions |
| `chat_messages` | `db/models/chat_messages.py` | Messages within a chat session |
| `chat_study_association` | `db/models/chat_link_study.py` | Many-to-many link between chat sessions and studies |
| `hypotheses` | `db/models/hypothesis.py` | One research hypothesis per chat session |
| `plans` | `db/models/plan.py` | One execution plan per hypothesis |
| `plan_steps` | `db/models/steps.py` | Ordered steps within a plan |
| `task_messages` | `db/models/Taskmessage.py` | Structured payload per executed step |
| `context_augmentations` | `db/models/contextaugmentation.py` | Domain-knowledge context attached to a task message |
| `ws_log` | `db/models/Wslog.py` | WebSocket event log per session |
| `workflow` | `db/models/workflow.py` | Named annotation-training workflows (nnUNet training queue) |
| `workflow_details` | `db/models/workflow_details.py` | Study/series membership within a workflow, with `train`/`annotated` flags |

`workflow` / `workflow_details` back the nnUNet training endpoints (`/workflow/...`, see below) —
they are unrelated to the Research Playground's `plans`/`plan_steps`, despite the similar name.

---

## Alembic Migrations

Schema changes are managed via Alembic.

### Running Migrations

```bash
# From the Backend directory
cd Backend

# Apply all pending migrations
alembic -c application_api/alembic.ini upgrade head

# Check current migration state
alembic -c application_api/alembic.ini current

# Show migration history
alembic -c application_api/alembic.ini history
```

`DATABASE_URL` must be set in the environment before running any `alembic` command — `env.py`
raises rather than falling back to a default, specifically so a forgotten `DATABASE_URL` cannot
silently target the wrong database.

### Creating a New Migration

```bash
# Auto-generate from model changes
alembic -c application_api/alembic.ini revision --autogenerate -m "add_new_field"

# Create an empty migration
alembic -c application_api/alembic.ini revision -m "manual_migration"
```

### Migration Files

Located in `Backend/application_api/migrations/versions/`. The migration configuration is
`Backend/application_api/alembic.ini` so schema history ships with the service that owns PostgreSQL
access. `Base.metadata` (in `db/base.py`) carries an explicit naming convention, and `env.py` sets
`compare_type=True` / `compare_server_default=True`, so `--autogenerate` can actually detect
column-level drift between the ORM and the live schema instead of silently missing it.

> ⚠️ Always verify autogenerated migrations before applying — Alembic may generate `DROP TABLE`
> statements if model imports are incorrect in `env.py`. The migration chain also contains two
> `TRUNCATE ... CASCADE` revisions from early development
> (`c1795081c45d_clear_main_data_tables.py`, `89424aedda49_clear_main_data_tables copy.py`) — see
> `database_audit.md` finding D-1 before restoring or migrating any database that predates them.

---

## Common Queries

```sql
-- View all Orthanc-synced studies for a patient
SELECT s.* FROM studies s JOIN patients p ON s.patient_id = p.patient_id
WHERE p.name ILIKE '%smith%';

-- View all named nnUNet training workflows
SELECT * FROM workflow;

-- Check annotation status for a workflow
SELECT study_uid, annotated FROM workflow_details WHERE task_name = 'MyWorkflow';

-- Filter unannotated cases in a workflow
SELECT study_uid FROM workflow_details WHERE task_name = 'MyWorkflow' AND annotated = FALSE;

-- Compare row counts against Orthanc's own statistics (see GET /sync/sync-status)
SELECT count(*) FROM studies;
```

---

## API Endpoints

Per the service-ownership boundary, the **Application API** (not MONAI Label) owns every database
route, including the nnUNet training-workflow endpoints:

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/workflow/{task_name}/add` | Add a study/series to a training workflow |
| `POST` | `/workflow/update-uids` | Update series/instance UIDs on a workflow record |
| `POST` | `/workflow/delete-multiple` | Delete multiple workflow records |
| `POST` | `/workflow/{task_name}/filter` | Get study/series/modality data for a workflow |
| `POST` | `/workflow/{task_name}/filter-annotated` | Get annotation status per study |
| `POST` | `/workflow/{task_name}/filter-uid` | Get study UIDs for a workflow |
| `POST` | `/workflow/add` | Register a new named workflow |
| `GET` | `/workflow/` | List all workflows |
| `DELETE` | `/workflow/delete` | Delete a workflow (cascades its `workflow_details` rows) |
| `POST` | `/workflow/{task_name}/update-train` | Update the train flag for a study |
| `GET` | `/workflow/{task_name}/get-train` | Get the train flag for a study |
| `POST` | `/workflow/{task_name}/train` | Kick off nnUNet training via MONAI Label (returns a job id) |
| `POST` | `/sync/sync-all` | Trigger a full reconciliation pass against Orthanc |
| `GET` | `/sync/sync-status` | Compare DB vs. Orthanc row counts |
| `POST` | `/sync/orthanc-webhook` | Receiver for the Orthanc Lua stable-series/deleted hooks |

The full endpoint catalogue (patients, studies, volumes, segmentations, models, chat, hypotheses,
plans, etc.) is in [API Reference](../api-reference/README.md).

---

## pgAdmin Access

To browse the database visually:

1. Install [pgAdmin](https://www.pgadmin.org/)
2. Connect using the credentials from your local `deploy/.env` (host `localhost`, port `5433` from
   the developer machine)
3. Navigate to: `Servers → PostgreSQL → workflow_db → Schemas → public → Tables`

---

## Backup & Restore

```bash
# Backup (container name is postgres-backend, not postgres)
docker exec -t postgres-backend pg_dump -U workflow_user workflow_db > backup.sql

# Restore
docker exec -i postgres-backend psql -U workflow_user workflow_db < backup.sql
```

`deploy/database/backup.sql` (mounted into the Postgres container's `docker-entrypoint-initdb.d/`)
is a placeholder pg_dump containing only `SET` statements and no actual schema or data — it is not a
usable restore point.

---

## See Also

- [Backend Documentation](../backend/README.md) — Backend service overview
- [Deployment Guide](../deployment/README.md) — How to start all services
- [API Reference](../api-reference/README.md) — Full API documentation
- [Database & Orthanc-Sync Audit](../backend/database_audit.md) — Known issues and their fixes
