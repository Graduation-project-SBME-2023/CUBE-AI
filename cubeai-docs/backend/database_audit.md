# Database & Orthanc-Sync Audit

Scope: `Backend/application_api/` (the only service with `DATABASE_URL`, per the service-ownership
boundary in `CLAUDE.md`) plus the Orthanc write path that feeds it. Every finding below was verified
by reading the cited file at the stated lines. Two root causes explain most of what follows:

1. **Three competing schema authorities.** The Alembic chain, the SQLAlchemy ORM models, and a
   `Base.metadata.create_all()` call inside the seeder all claim to define the schema. Nothing is
   authoritative, so drift accumulates unnoticed and `alembic revision --autogenerate` cannot be
   trusted.
2. **The Orthanc→Postgres write path is a fire-and-forget Lua `HttpPost`.** No retry, no durable
   cursor, no reconciliation job. Every orphaned row, race condition, and silent data drop below
   descends from this one design choice.

## How to read this document

Each finding carries an **evidence label**, because a defect you can point to in the source and a
risk that is merely plausible from the source deserve different confidence and different wording:

| Label | Means | Wording rule |
|---|---|---|
| `[Confirmed]` | Verified by reading the cited file | States consequences as fact |
| `[Needs repro]` | Source makes it plausible; runtime behaviour not yet observed | Says "can" / "risks", never "does" — a reproduction procedure is given |
| `[Policy]` | A security/operations decision, not a code defect | Framed as a requirement, with the trade-off named |

Findings are `D-1` through `D-34`, grouped by theme, plus a secondary `Appendix A` (`P-1`–`P-8`) for
pure PACS/ingress issues that surfaced during the audit but sit outside the database's boundary.

---

## Findings index

| ID | Title | Sev | Evidence |
|---|---|---|---|
| D-1 | Two `TRUNCATE ... CASCADE` migrations permanently in the chain | Critical | Confirmed |
| D-2 | Seeder runs `create_all()` outside Alembic | Critical | Confirmed |
| D-3 | ORM/migration schema disagree on ~25 columns | High | Confirmed source / Needs repro live-DB |
| D-4 | `chat_study_association` has no primary key | High | Confirmed |
| D-5 | `plans.hypothesis_id` unique only in the ORM | High | Confirmed |
| D-6 | `UNIQUE (name, birth_date)` is the wrong patient key | High | Confirmed |
| D-7 | Alembic ships a working hardcoded DSN | High | Confirmed |
| D-8 | No naming convention / type comparison in Alembic | Medium | Confirmed |
| D-9 | A migration seeds demo data into production | Medium | Confirmed |
| D-10 | A downgrade path destroys chat identity | Medium | Confirmed |
| D-11 | Sync trigger is a fire-and-forget Lua webhook | Critical | Confirmed |
| D-12 | Duplicate-key recovery rolls back the whole session | High | Confirmed |
| D-13 | Duplicate-key detection is string matching | Medium | Confirmed |
| D-14 | No mutual exclusion on concurrent sync | High | Confirmed design / Needs repro loss |
| D-15 | SEGs silently dropped when parent volume missing | Critical | Confirmed |
| D-16 | Metadata sync is write-once | Medium | Confirmed |
| D-17 | Deletes are Orthanc-first, non-transactional | Critical | Confirmed |
| D-18 | No reconciliation job exists or is called | High | Confirmed |
| D-19 | Connections held across network I/O | High | Confirmed shape / Needs repro exhaustion |
| D-20 | `workflow.py` bypasses SQLAlchemy transactions | High | Confirmed pattern / Needs repro corruption |
| D-21 | 404s swallowed into 500s in `workflow.py` | High | Confirmed |
| D-22 | `upsert_model` is a check-then-insert race | High | Confirmed |
| D-23 | Dead exception handler (wrong driver's `IntegrityError`) | Medium | Confirmed |
| D-24 | N+1 query in plan/step listing | Medium | Confirmed |
| D-25 | `/health` breaks on multi-head `alembic_version` | Medium | Confirmed |
| D-26 | Missing indexes on filtered/ordered columns | Medium | Confirmed |
| D-27 | Coercions silently discard DICOM values | Medium | Confirmed |
| D-28 | Orphaned rows with no working cascade | Medium | Confirmed |
| D-29 | `deploy/.env` tracked with live credentials | Critical | Confirmed fact / Policy remediation |
| D-30 | No authentication on any PHI route | Critical | Confirmed absence / Policy requirement |
| D-31 | Release-candidate PostgreSQL holds clinical data | High | Confirmed fact / Policy upgrade |
| D-32 | CPU/GPU stacks use different data volumes | High | Confirmed |
| D-33 | Migration image and runtime image are different code | High | Confirmed |
| D-34 | No DB test infra; 5 contract tests broken | High | Confirmed |

---

## Schema authority & migrations

### D-1 — Two `TRUNCATE ... CASCADE` migrations permanently in the chain

**Severity:** Critical · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/migrations/versions/c1795081c45d_clear_main_data_tables.py:21-27`;
`Backend/application_api/migrations/versions/89424aedda49_clear_main_data_tables copy.py:21-27`

**What happens:** Both revisions run:
```sql
TRUNCATE TABLE radiomics_features CASCADE;
TRUNCATE TABLE segmentations CASCADE;
TRUNCATE TABLE volume_series CASCADE;
TRUNCATE TABLE studies CASCADE;
TRUNCATE TABLE patients CASCADE;
```
Alembic will **not** re-run an already-applied revision. The accurate risk is: any database restored
with `alembic_version` stamped at a revision that precedes either of these loses all rows in those
five tables, once, the next time `alembic upgrade head` runs against it. Both `downgrade()` bodies are
`pass`, so there is no in-band recovery. One file is literally named
`89424aedda49_clear_main_data_tables copy.py` — a space in the filename.

**Why:** These look like one-off cleanup scripts someone ran through `alembic revision` instead of a
raw `psql` session, so they became permanent, replayable migration steps.

**Fix:** Do not attempt to delete or rewrite historical migrations once they may have been applied
anywhere. Instead:
1. Confirm via Phase 0 (below) which environments have `alembic_version` before `c1795081c45d`.
2. For any such environment, restore its last good backup **before** running `upgrade head`.
3. Add a migration-chain guard (a CI check or a `pre-migrate` script) that greps new migration files
   for `TRUNCATE`/`DROP TABLE` and requires explicit human sign-off before merge.

---

### D-2 — Seeder runs `create_all()` outside Alembic

**Severity:** Critical · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/seeders/model.py:67`

**What happens:**
```python
def seed_models():
    Base.metadata.create_all(bind=sync_engine)
```
This runs immediately after `alembic upgrade head` (compose: `db-migrate` → `db-seed`). It creates any
ORM-declared table or column that migration history doesn't have, **without** recording anything in
`alembic_version`. The database's actual schema then permanently diverges from what the migration
history claims it is — which is the mechanism that let D-3 accumulate unnoticed.

**Fix:** Delete the `create_all()` call. If a genuinely missing table is discovered, write a real
Alembic migration for it and let `db-migrate` create it, so `alembic_version` stays authoritative.

```python
def seed_models():
    db = SessionLocal()
    if db.query(model.Model).count() != 0:
        logger.info("Models already seeded. Skipping.")
        db.close()
        return
    ...
```

---

### D-3 — ORM and migration-derived schema disagree on ~25 columns

**Severity:** High · **Evidence:** `[Confirmed]` source drift · `[Needs repro]` live-DB state
**Where:** `0342ce56c4b1_add_orthanc_ids.py:22-25`; `a3f16d77d6eb_update_tables_constrains.py:22-28`;
`82dbfa7a61e0_update_models_schema_to_match_monai_api.py:31-34`; `Backend/application_api/db/models/*`

**What happens:** Reading the ORM models against the migration history that created each column shows
disagreement in at least:

| Column | ORM says | Migration says |
|---|---|---|
| `patients.orthanc_patient_id` | `nullable=False` | added `nullable=True`, never tightened |
| `studies.orthanc_study_id` | `nullable=False` | added `nullable=True`, never tightened |
| `volume_series.orthanc_series_id` | `nullable=False` | added `nullable=True`, never tightened |
| `segmentations.orthanc_series_id` | `nullable=False` | added `nullable=True`, never tightened |
| `patients.birth_date` | `nullable=False` | `a3f16d77d6eb` made it nullable |
| `studies.patient_id` | not declared nullable | migration has its own constraint history |
| `volume_series.num_slices`, `.created_at`, `.modality` | ORM defaults differ from DDL |
| `models.type`, `.name`, `.modality`, `.config` | `82dbfa7a61e0` changed shape post-ORM |
| `radiomics_features.features`; `chat_sessions` timestamps; `plan_steps.status`; `ws_log.msg_type` | similar drift |

This is a **source-level** claim: it compares two textual definitions, not the running database. Only
Phase 0's schema capture settles whether the live database currently matches the ORM, the migration
history, or neither.

**Fix:** Run the verification below, then write one reconciliation migration that makes the DB match
whichever side (ORM or DDL) is intended, and update the other side to match.

---

### D-4 — `chat_study_association` has no primary key

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `6664d514406c_create_hypothesis_plan_plan_step_tables.py:109,120`;
`Backend/application_api/db/models/chat_link_study.py:5-10`;
`Backend/application_api/repositories/chat_sessions.py:128-130`;
`f1a2b3c4d5e6_add_application_api_indexes.py:38-49`

**What happens:** The table was created with a composite PK in an earlier migration. During the
UUID conversion, `chat_id` was dropped and re-added as a new UUID column:
```python
op.drop_column('chat_study_association', 'chat_id')
op.alter_column('chat_study_association', 'chat_id_new', new_column_name='chat_id', nullable=False)
```
Dropping the PK-participating column drops the primary key with it; it was never recreated, and the
ORM's `Table(...)` declaration for this association has no `PrimaryKeyConstraint` either. Duplicate
`(chat_id, study_uid)` rows are therefore possible at the database level; the only defense is
application-side de-duplication in `chat_sessions.py:128-130`, which is not atomic. A later migration
adds two **non-unique** indexes on the same columns — this reduces read cost but does not restore the
constraint.

**Fix:**
```python
def upgrade():
    op.create_primary_key(
        "chat_study_association_pkey",
        "chat_study_association",
        ["chat_id", "study_uid"],
    )
```
Run a de-duplication `DELETE` first if any duplicates already exist; check with:
```sql
SELECT chat_id, study_uid, count(*) FROM chat_study_association
GROUP BY chat_id, study_uid HAVING count(*) > 1;
```

---

### D-5 — `plans.hypothesis_id` unique only in the ORM

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/db/models/plan.py:19-24`;
`6664d514406c_create_hypothesis_plan_plan_step_tables.py:185,188-197`

**What happens:** The ORM declares:
```python
hypothesis_id = Column(UUID(as_uuid=True), ForeignKey("hypotheses.id", ondelete="CASCADE"),
                       unique=True, nullable=False)
```
but the migration that creates the `plans` table never adds a unique constraint on that column — only
the sibling `hypotheses` table gets `sa.UniqueConstraint('chat_id')`. Since `Hypothesis.plan` is
declared `uselist=False`, a second `Plan` row for the same hypothesis will not be rejected by the
database and will corrupt that relationship load at the ORM level.

**Fix:**
```python
def upgrade():
    op.create_unique_constraint(
        "uq_plans_hypothesis_id", "plans", ["hypothesis_id"]
    )
```

---

### D-6 — `UNIQUE (name, birth_date)` is the wrong key for patient identity

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `04eab3c05281_initial_schema.py:37`; `Backend/application_api/repositories/orthanc_sync.py:61-105`

**What happens:** The initial schema still carries:
```sql
unique (name, birth_date)
```
while `orthanc_sync.py` identifies patients by `orthanc_patient_id`, not by name+DOB. PostgreSQL
treats `NULL` as distinct in a unique constraint, so multiple `("Unknown", NULL)` rows coexist fine —
**the collision requires a matching name and a matching non-null birth date**, which happens routinely
for real name/DOB coincidences and for anonymised cohorts sharing a synthetic date of birth. On
collision the insert fails with a unique-violation, and (per D-12) the recovery path looks up by
`orthanc_patient_id`, finds nothing, and raises `RuntimeError`, aborting the whole study sync.

**Fix:**
```python
def upgrade():
    op.drop_constraint("patients_name_birth_date_key", "patients", type_="unique")
```
Patient identity should be `orthanc_patient_id` alone (already `unique=True, index=True` on the
model) — no other uniqueness constraint on clinical attributes is clinically correct.

---

### D-7 — Alembic ships a working hardcoded DSN

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/alembic.ini:87`; `Backend/application_api/migrations/env.py:39-42`

**What happens:**
```ini
sqlalchemy.url = postgresql+psycopg2://workflow_user:password@localhost:5433/workflow_db
```
`env.py` only overrides this when `DATABASE_URL` is set:
```python
database_url = os.getenv("DATABASE_URL")
sync_url = database_url.replace("asyncpg", "psycopg2") if database_url else None
if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url)
```
Running `alembic upgrade head` from a shell with `DATABASE_URL` unset silently targets whatever is on
host port 5433 with the committed credentials — which, given `docker-compose.backend.cpu.yml`, is
frequently the real project database exposed on that same port. The override itself is also a blind
substring `.replace("asyncpg", "psycopg2")` across the entire URL, so a password containing the
literal substring "asyncpg" would be corrupted.

**Fix:**
```python
database_url = os.environ["DATABASE_URL"]  # raise loudly if unset, don't fall back
from urllib.parse import urlsplit, urlunsplit
parts = urlsplit(database_url)
sync_url = urlunsplit(parts._replace(scheme=parts.scheme.replace("+asyncpg", "+psycopg2")))
config.set_main_option("sqlalchemy.url", sync_url)
```
Remove the credential from `alembic.ini` entirely; replace with a placeholder comment.

---

### D-8 — No naming convention or type comparison in Alembic

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/db/base.py:6`; `Backend/application_api/migrations/env.py`;
`0342ce56c4b1_add_orthanc_ids.py:33-35`

**What happens:** `Base = declarative_base()` sets no `naming_convention`, so autogenerate cannot
predict constraint/index names — this is why `0342ce56c4b1` has to pass `op.create_index(None, ...)`
and rely on Postgres's default naming. `env.py`'s `context.configure(...)` calls also omit
`compare_type=True` and `compare_server_default=True`, so `--autogenerate` silently ignores exactly
the kind of drift catalogued in D-3. `downgrade()` is a bare `pass` in five separate revisions,
compounding the same "nothing enforces the mirror" problem in the other direction.

**Fix:**
```python
# db/base.py
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
Base = declarative_base()
Base.metadata.naming_convention = convention
```
```python
# migrations/env.py, both online and offline configure() calls
context.configure(
    ...,
    compare_type=True,
    compare_server_default=True,
)
```

---

### D-9 — A migration seeds demo data into production

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `a57c5c5784a7_update_chat_sessions.py:19,28`

**What happens:** This revision (`down_revision = '89424aedda49'`, i.e. before the later UUID
conversion in `6664d514406c`) inserts three fixture chat sessions — "General AI Discussion", "EEG
Analysis Chat", "ML Fairness Review" — using `column('chat_id', Integer)`, which is correct at this
point in the chain since the UUID conversion hasn't happened yet. Every fresh deployment that runs the
full migration chain gets this fixture data seeded into what is meant to be a clean production
database.

**Fix:** Remove the `INSERT` from `upgrade()` in a follow-up migration, or gate it behind an
environment check if it's needed for local dev:
```python
import os
def upgrade():
    if os.environ.get("SEED_DEMO_CHAT_DATA") == "1":
        op.bulk_insert(chat_sessions_table, DEMO_ROWS)
```
Prefer moving demo fixtures into the seeder (`seeders/`), which already has an idempotency check, and
removing them from the migration chain entirely.

---

### D-10 — A downgrade path destroys chat identity

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `6664d514406c_create_hypothesis_plan_plan_step_tables.py:227-238`

**What happens:**
```python
op.execute("ALTER TABLE chat_sessions ALTER COLUMN chat_id TYPE INTEGER USING 1")
```
Every row's `chat_id` becomes the literal integer `1` on downgrade — self-documented in the source as
`"you'll lose data"`. The subsequent `create_foreign_key` in the same downgrade cannot succeed once
every row shares the same key.

**Fix:** No safe automated fix exists for a destructive downgrade after the fact; document it as
irreversible and add a guard:
```python
def downgrade() -> None:
    raise NotImplementedError(
        "This revision's downgrade is destructive (collapses chat_id to a single value) "
        "and is intentionally disabled. Restore from a pre-upgrade backup instead."
    )
```

---

## Sync correctness — how rows are created and destroyed

### D-11 — Sync trigger is a fire-and-forget Lua webhook

**Severity:** Critical · **Evidence:** `[Confirmed]`
**Where:** `Backend/orthanc/lua/on-stable-series.lua:11-18`

**What happens:**
```lua
local function post_webhook(payload, description)
    pcall(function()
        HttpPost(WEBHOOK_URL, DumpJson(payload, true), JSON_HEADERS)
    end)
end
```
`pcall` swallows every failure — network error, application-api being mid-restart, a 500 response —
with no retry, no dead-letter queue, and no log line (the `description` parameter is accepted and
never used). If the webhook fails, that stable-series/stable-study/delete event is lost forever; there
is no other mechanism that discovers it (see D-18).

**Fix:** Replace push-only delivery with Orthanc's durable `/changes` API, which the application-api
can poll and track a cursor for — this survives both application-api restarts and Orthanc restarts
without losing events:
```python
# a periodic task in application-api
async def poll_orthanc_changes(db, session):
    cursor = await get_last_change_seq(db)
    async with session.get(f"{ORTHANC_URL}/changes?since={cursor}&limit=100") as resp:
        data = await resp.json()
    for change in data["Changes"]:
        await handle_change(db, change)
        await save_change_seq(db, change["Seq"])
    if not data["Done"]:
        await poll_orthanc_changes(db, session)  # more pages waiting
```
Keep the Lua hook as a low-latency fast path, but make the poller the source of truth.

---

### D-12 — Duplicate-key recovery rolls back the whole session

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/orthanc_sync.py:44-54`

**What happens:**
```python
async def _refetch_after_duplicate(db, model_class, where_clause):
    await db.rollback()
    result = await db.execute(select(model_class).where(where_clause))
    record = result.scalar_one_or_none()
    if record:
        return record
    raise RuntimeError(f"Could not re-fetch {model_class.__name__} after duplicate error.")
```
The refetched row itself is fine — it's committed data with valid foreign keys. The actual damage is:
(a) `db.rollback()` discards **every pending, unflushed object in the same sync unit**, including
e.g. a `Patient` flushed moments earlier in `sync_study_to_db`; (b) ORM instances in the session are
expired after rollback, so the caller's later `commit()` covers less state than it appears to; (c)
when the unique violation came from a **different** constraint than the one being queried against —
exactly the D-6 scenario — the refetch-by-lookup-key returns nothing and the function raises
`RuntimeError`, aborting the entire study sync rather than just the one row.

**Fix:** Use `INSERT ... ON CONFLICT DO NOTHING` / `DO UPDATE` at the statement level instead of
catching the exception after the fact, so there's nothing to roll back:
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = pg_insert(Patient).values(**patient_data).on_conflict_do_nothing(
    index_elements=["orthanc_patient_id"]
)
await db.execute(stmt)
result = await db.execute(select(Patient).where(Patient.orthanc_patient_id == orthanc_patient_id))
return result.scalar_one()
```
This requires D-6's constraint removed first, or the `ON CONFLICT` target is ambiguous.

---

### D-13 — Duplicate-key detection is string matching

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/orthanc_sync.py:38-41`

**What happens:**
```python
def _is_duplicate_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "duplicate key" in msg or "unique constraint" in msg
```
This depends on the exact wording of the database driver's error message, which varies by locale and
driver version.

**Fix:**
```python
from sqlalchemy.exc import IntegrityError

def _is_duplicate_error(exc: Exception) -> bool:
    if not isinstance(exc, IntegrityError):
        return False
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) == "23505"
```

---

### D-14 — No mutual exclusion on concurrent sync

**Severity:** High · **Evidence:** `[Confirmed]` design · `[Needs repro]` data loss
**Where:** `Backend/orthanc/lua/on-stable-series.lua:28-52`; `Backend/application_api/api/routes/sync.py:110-190`

**What happens:** `OnStableSeries` and `OnStableStudy` fire independently per study, each posting a
separate webhook that becomes an independent FastAPI `BackgroundTask` with its own DB session, and
`APP_API_WORKERS: 2` (per `CLAUDE.md`) can put concurrent syncs in different processes entirely. There
is no advisory lock (`pg_advisory_xact_lock` or equivalent) anywhere in the codebase. The design
*anticipates* the resulting race via D-12/D-13's duplicate-key handling rather than preventing it, so
rollback-on-conflict is treated as a normal operating mode rather than an exceptional one.

**Reproduction procedure to move this to `[Confirmed]`:** fire `stable_study` and `stable_series` for
the same study concurrently (e.g. via two parallel `curl` calls to `/sync/orthanc-webhook`) against a
build with `APP_API_WORKERS: 2`, then check whether the resulting row count for that study's patient
is exactly 1 across 50 repeated trials.

**Fix:**
```python
async def sync_study_to_db(db, orthanc_study_id, study_uid=None, session=None):
    lock_key = hash(study_uid or orthanc_study_id) & 0x7FFFFFFF
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    # ... existing body, lock auto-releases at transaction end
```

---

### D-15 — SEGs silently dropped when parent volume missing

**Severity:** Critical · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/orthanc_sync.py:250-259`

**What happens:**
```python
if not v_result.scalar_one_or_none():
    logger.warning("Skipping SEG %s: Referenced volume %s not in DB.", label_uid, volume_uid)
    return None
```
This is exactly the ordering that occurs when a SEG's `stable_series` webhook is delivered before its
source series' webhook (very plausible: Orthanc considers a series "stable" independently for each,
and D-14 shows delivery order is not guaranteed). The segmentation is dropped permanently — nothing
retries it, nothing queues it, and the only trace is a WARNING log line.

**Fix:** Queue the orphaned SEG for retry instead of discarding it:
```python
if not v_result.scalar_one_or_none():
    await db.execute(
        pg_insert(PendingSegmentation).values(
            label_uid=label_uid, orthanc_series_id=orthanc_series_id,
            volume_uid=volume_uid, ...
        ).on_conflict_do_nothing(index_elements=["label_uid"])
    )
    logger.warning("Deferred SEG %s: volume %s not yet synced.", label_uid, volume_uid)
    return None
```
Then re-attempt every `pending_segmentations` row each time `_get_or_create_volume` successfully
creates a new volume, and also from the reconciliation job in D-18.

---

### D-16 — Metadata sync is write-once

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/orthanc_sync.py:122-134,171-190,232-248`

**What happens:** All four `_get_or_create_*` helpers only fill fields that are currently empty:
```python
if description and not study.description:
    study.description = description
    updated = True
```
A corrected DICOM re-sent to Orthanc (e.g. a fixed `SeriesDescription`) never updates the
already-synced row.

**Fix:** Decide intent explicitly rather than defaulting to write-once. If Orthanc should always win
for DICOM-sourced fields (leaving room for manually-added fields the sync never touches):
```python
if description:
    study.description = description
    updated = True
```
If manual edits must be preserved, add an explicit `is_manually_edited` flag per field instead of
inferring it from nullness.

---

### D-17 — Deletes are Orthanc-first, non-transactional

**Severity:** Critical · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/{volume,segmentation,study,patient}.py`;
`Backend/application_api/services/orthanc_client.py:21-22`;
`Backend/application_api/api/routes/volume.py:148-150`

**What happens:** All four delete paths follow the same shape:
```python
deleted_from_orthanc, error_msg = await delete_orthanc_resource("series", orthanc_series_id)
if not deleted_from_orthanc:
    return False, error_msg
await db.delete(volume)
await db.commit()
```
If the Orthanc call succeeds and the subsequent DB commit fails, the imaging is already gone from PACS
while a dangling row survives with no imaging behind it. Separately,
`delete_orthanc_resource` treats a falsy `resource_id` as success:
```python
if not resource_id:
    return True, None
```
which per D-3 is a legal state (the column is nullable in the live DB even though the ORM says
otherwise) — so such a row can be deleted from Postgres while its actual PACS copy is left behind,
orphaned. And `delete_volume` removes only the volume's own Orthanc series while the DB's
`ON DELETE CASCADE` silently drops the child `segmentations` rows — the derived DICOM SEG stays in
Orthanc, orphaned, and D-15's guard then refuses to ever re-adopt it since its parent volume row is
gone. The route docstring at `volume.py:148-150` claims the cascade handles cleanup; it does not.

**Fix:** Delete from the database first inside a transaction, and only delete from Orthanc after the
commit succeeds — if the Orthanc delete then fails, log it for a cleanup job rather than reporting
total success or total failure to the caller:
```python
async def delete_volume(db, volume_uid):
    result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_uid))
    volume = result.scalar_one_or_none()
    if not volume:
        return False, "Volume not found"

    orthanc_series_id = volume.orthanc_series_id
    seg_orthanc_ids = [
        s.orthanc_series_id for s in
        (await db.execute(select(Segmentation).where(
            Segmentation.volume_series_instance_uid == volume_uid))).scalars()
    ]
    await db.delete(volume)  # cascades segmentations in DB
    await db.commit()

    for oid in [orthanc_series_id, *seg_orthanc_ids]:
        ok, err = await delete_orthanc_resource("series", oid)
        if not ok:
            logger.error("Orphaned Orthanc series %s after DB delete: %s", oid, err)
            await enqueue_orthanc_cleanup(oid)  # retried by a background job
    return True, None
```
Also stop treating a falsy `resource_id` as unconditional success — log it instead:
```python
if not resource_id:
    logger.warning("Delete requested with no Orthanc resource id; nothing to clean up in PACS.")
    return True, None
```

---

### D-18 — No reconciliation job exists or is called

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/main.py:35-44`; `Backend/application_api/api/routes/sync.py:41-99,107`;
`Backend/application_api/repositories/orthanc_sync.py:498-525`

**What happens:** `sync_all_from_orthanc` is reachable only from `POST /sync/sync-all`. FastAPI's
`lifespan` starts no periodic or startup sync, and a repo-wide grep across `Frontend/**` for
`sync-all` or `sync-status` finds **zero callers** — nothing in the product ever invokes it.
`GET /sync/sync-status` is a detector, not a repairer, and even as a detector it only compares a raw
study count:
```python
"synced": studies_count == orthanc_stats.get("CountStudies", 0) if orthanc_stats else None
```
never series, never segmentations, never per-UID. `fetch_dicom_uid_for_deletion` was written to solve
the delete-ordering TOCTOU (see the comment at `sync.py:126-127`) and is never called.

**Fix:** Add a scheduled reconciliation task (APScheduler, or a simple `asyncio.create_task` loop in
`lifespan`) that runs the `/changes`-cursor poller from D-11's fix on an interval, plus a lighter daily
full comparison that logs (and optionally repairs) any UID present in Orthanc but absent from Postgres
and vice versa:
```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    reconcile_task = asyncio.create_task(periodic_reconciliation(interval_seconds=300))
    ...
```

---

## Runtime & connection handling

### D-19 — Connections held across network I/O

**Severity:** High · **Evidence:** `[Confirmed]` code shape · `[Needs repro]` exhaustion
**Where:** `Backend/application_api/db/session.py:12-19,33`; `Backend/application_api/repositories/patient.py:101-111`

**What happens:**
```python
async def get_db():
    async with AsyncSessionLocal() as session:
        await session.connection()
        yield session
```
`await session.connection()` opens a pooled connection and starts a transaction on **every** request,
even ones that never touch the database. The four delete repositories then hold that connection across
an `await` on Orthanc, bounded only by `ORTHANC_REQUEST_TIMEOUT=30.0`. The pool is `DB_POOL_SIZE=5 +
DB_MAX_OVERFLOW=5` **per worker** — with `APP_API_WORKERS=2` that's roughly 20 connections across the
process group, not 10, so "the Nth concurrent delete exhausts the pool" only applies within a single
worker and depends on load-balancer distribution. There is no `pool_recycle` and no
`statement_timeout`.

**Reproduction procedure:** fire 11 concurrent `DELETE /volumes/{uid}` requests pinned to one worker
(e.g. via a sticky session header or by scaling to `APP_API_WORKERS=1` for the test) against a slow or
unreachable Orthanc mock, and confirm the 11th request blocks for `DB_POOL_TIMEOUT` and then fails.

**Fix:** Don't open a connection eagerly in `get_db`; let SQLAlchemy lazily acquire one on first query:
```python
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```
Add `pool_recycle` and a statement timeout:
```python
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=1800,
    connect_args={"server_settings": {"statement_timeout": "30000"}},
)
```
And restructure the delete repositories so the DB transaction does not span the Orthanc call (see the
D-17 fix, which already does this).

---

### D-20 — `workflow.py` bypasses SQLAlchemy transactions

**Severity:** High · **Evidence:** `[Confirmed]` pattern · `[Needs repro]` corruption
**Where:** `Backend/application_api/api/routes/workflow.py:96-102`

**What happens:**
```python
async def get_db_connection():
    async with engine.connect() as pooled_connection:
        raw_connection = await pooled_connection.get_raw_connection()
        yield InstrumentedWorkflowConnection(raw_connection.driver_connection)
```
This checks out a connection from the shared SQLAlchemy pool and then hands the caller the bare
asyncpg driver connection, running hand-written SQL against it directly — outside SQLAlchemy's own
transaction bookkeeping. This is an unsupported pattern with undefined transaction semantics on a pool
that other, correctly-behaved code also uses concurrently. Whether this actually corrupts pool state
under load has not been observed and needs a reproduction test rather than being asserted as fact.

**Reproduction procedure:** run the `workflow` routes under concurrent load while a separate correctly
-behaved route (e.g. `GET /patients/`) runs in parallel; check for unexpected transaction-state errors
or `InFailedSqlTransaction` surfacing on the unrelated route.

**Fix:** Route `workflow.py` through the normal `AsyncSession` dependency and SQLAlchemy Core, which
supports parameterized raw SQL without leaving the pool's bookkeeping:
```python
from sqlalchemy import text

@router.put("/{task_name}")
async def update_workflow_record(task_name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("UPDATE workflow_details SET ... WHERE task_name = :task_name"),
        {"task_name": task_name},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Workflow record not found")
    await db.commit()
```

---

### D-21 — 404s swallowed into 500s in `workflow.py`

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/api/routes/workflow.py:106,111-113,130,181-188,329-340,355-362,374-381`

**What happens:** Four separate handlers wrap the whole body in `except Exception`, which also catches
the `HTTPException(404, ...)` the same `try` block just raised:
```python
try:
    result = await conn.execute(query, *params)
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Workflow record not found")
    ...
except Exception as e:
    raise HTTPException(status_code=500, detail="Failed to update workflow record")
```
Every legitimate 404 becomes a 500. Separately, `data: WorkflowRequest = None` is a default that is
then dereferenced unconditionally (`data.task_name`), and the existence check reads the **path**
parameter `task_name` while the subsequent INSERT reads the **body**'s `data.task_name` — the two can
disagree, defeating the existence guard entirely.

**Fix:**
```python
try:
    result = await conn.execute(query, *params)
except SomeDriverError as e:
    raise HTTPException(status_code=500, detail="Failed to update workflow record") from e
if result == "UPDATE 0":
    raise HTTPException(status_code=404, detail="Workflow record not found")
```
Move the `HTTPException(404)` raise outside the `try`, or catch only the specific driver exception
class. Also require `data: WorkflowRequest` (no default) and use the same source (path or body,
consistently) for both the existence check and the write.

---

### D-22 — `upsert_model` is a check-then-insert race

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/model.py:50-93`; `Backend/application_api/services/monai_sync.py:21-31`

**What happens:**
```python
async def upsert_model(db, model_data):
    result = await db.execute(select(Model).where(Model.name == model_data["name"]))
    existing = result.scalar_one_or_none()
    if not existing:
        db.add(Model(**model_data))
        await db.commit()
        return "inserted"
```
Two concurrent `/models/sync` calls both see `None` and both attempt the insert; the second hits the
unique-name violation with **no try/except anywhere in this function**, surfacing as an unhandled 500.
The correct `ON CONFLICT` version is sitting commented out directly above this code. `monai_sync.py`
calls this in a loop with one `commit()` per model, so a failure partway through leaves the table
half-updated with no rollback of prior iterations.

**Fix:**
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

async def upsert_model(db, model_data):
    stmt = pg_insert(Model).values(**model_data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={k: stmt.excluded[k] for k in model_data if k != "name"},
    )
    await db.execute(stmt)
    await db.commit()
    return "upserted"
```

---

### D-23 — Dead exception handler (wrong driver's `IntegrityError`)

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/hypothesis.py:3-4,31`

**What happens:**
```python
from psycopg2 import IntegrityError
from psycopg2 import IntegrityError  # duplicate import
...
except IntegrityError as e:
    raise HTTPException(status_code=409, detail=...)
```
The application runs on `asyncpg` (per `DATABASE_URL: postgresql+asyncpg://...`), which raises
`sqlalchemy.exc.IntegrityError`, not `psycopg2.IntegrityError`. This `except` clause never matches;
every real conflict falls through to a generic 500.

**Fix:**
```python
from sqlalchemy.exc import IntegrityError
...
except IntegrityError as e:
    await db.rollback()
    raise HTTPException(status_code=409, detail="Conflicting hypothesis state") from e
```

---

### D-24 — N+1 query in plan/step listing

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/api/routes/plan.py:84-93`

**What happens:**
```python
plans = await get_plans_for_hypothesis(db, hypothesis_id, skip=skip, limit=limit)
for plan in plans:
    steps_result = await db.execute(
        select(PlanStep).where(PlanStep.plan_id == plan.id)
        .options(selectinload(PlanStep.task_message).selectinload(TaskMessage.context_augmentation))
    )
```
One extra round trip per plan, up to `limit` (100) plans, and the inner steps query has no pagination
of its own.

**Fix:**
```python
from sqlalchemy.orm import selectinload

result = await db.execute(
    select(Plan)
    .where(Plan.hypothesis_id == hypothesis_id)
    .options(
        selectinload(Plan.steps)
        .selectinload(PlanStep.task_message)
        .selectinload(TaskMessage.context_augmentation)
    )
    .offset(skip).limit(limit)
)
plans = result.scalars().all()
```

---

### D-25 — `/health` breaks on multi-head `alembic_version`

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/main.py:110-119`; `106e8488b24c_merge_heads.py`

**What happens:**
```python
revision = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
```
`alembic_version` holds one row per **head**. The chain has already branched and merged once
(`106e8488b24c_merge_heads.py` has `down_revision = ('91794855a7a0', 'c04a22da5013')`), which is
harmless today because the merge collapsed it back to one head — but the *next* time the chain
branches without a merge migration, this table has more than one row and `.scalar_one()` raises,
turning `/health` into a 500. Compose uses `/health` as the readiness gate for `application-api`, and
`agents` in turn depends on `application-api: service_healthy`, so this failure mode cascades.

**Fix:**
```python
revisions = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
return {"status": "ready", "migration": ",".join(sorted(revisions))}
```

---

### D-26 — Missing indexes on filtered/ordered columns

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/db/models/*` (no corresponding DDL in `migrations/versions/`)

**What happens:** These columns are filtered or ordered on in live queries but have no index:
`plan_steps.plan_id` (FK, filtered in `repositories/steps.py:31,87` and `plan.py:161`, and in D-24's
N+1), `plans.hypothesis_id` (FK, filtered in `repositories/plan.py:121`),
`task_messages.context_augmentation_id` (FK, joined in `repositories/contextaugmentation.py:73`),
`segmentations.name` (FK to `models.name`, filtered in `segmentation.py:136`, grouped in `:183`),
`hypotheses.created_at` (ordered `DESC` in `repositories/hypothesis.py:64`), `ws_log.created_at`
(ordered `DESC` in `repositories/Wslog.py:158`).

**Fix:**
```python
def upgrade():
    op.create_index("ix_plan_steps_plan_id", "plan_steps", ["plan_id"])
    op.create_index("ix_plans_hypothesis_id", "plans", ["hypothesis_id"])
    op.create_index("ix_task_messages_context_augmentation_id", "task_messages", ["context_augmentation_id"])
    op.create_index("ix_segmentations_name", "segmentations", ["name"])
    op.create_index("ix_hypotheses_created_at", "hypotheses", ["created_at"])
    op.create_index("ix_ws_log_created_at", "ws_log", ["created_at"])
```

---

### D-27 — Coercions silently discard DICOM values

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/orthanc_sync.py:25-35,70,198`;
`Backend/application_api/repositories/patient.py:121-136`;
`Backend/application_api/repositories/Wslog.py:169-177`;
`Backend/application_api/repositories/segmentation.py:176-199`

**What happens:** Several silent-loss patterns in one file: `PatientSex` values other than `M`/`F` are
dropped to `NULL` even though the check constraint that once justified this restriction was removed by
`db570196426f_remove_gender_check_constraint.py`; `num_slices=max(1, n)` records a series with zero
instances as having one; every DICOM date-parse failure falls back to `datetime.utcnow()`, so a
malformed `StudyDate` is silently recorded as "now" (and `utcnow()` is deprecated in Python 3.12+).
Separately: `search_patients(..., age=...)` accepts an `age` parameter that is never used in the
query; `delete_logs_by_session` fetches an unbounded result set then deletes rows one at a time instead
of a single `DELETE ... WHERE`; `get_segmentation_stats` runs two unbounded full-table `GROUP BY`
scans with no limit, exposed directly at `GET /segmentations/stats/`.

**Fix (representative, apply the same pattern to each):**
```python
def _parse_dicom_datetime(date_str, time_str):
    if not date_str:
        return None  # let the caller decide the default, don't fabricate "now"
    try:
        ...
    except Exception:
        logger.warning("Unparseable DICOM datetime: date=%s time=%s", date_str, time_str)
        return None
```
```python
async def delete_logs_by_session(db, session_id):
    await db.execute(delete(WSLog).where(WSLog.session_id == session_id))
    await db.commit()
```

---

### D-28 — Orphaned rows with no working cascade

**Severity:** Medium · **Evidence:** `[Confirmed]`
**Where:** `Backend/application_api/repositories/steps.py:160`;
`Backend/application_api/db/models/Taskmessage.py:37`;
`Backend/application_api/db/models/segmentation.py:12`

**What happens:** `steps.py:160` comments `# cascade handles TaskMessage + ContextAugmentation`, but
`TaskMessage.context_augmentation` has no ORM-level cascade declared and the underlying FK is
`ondelete="SET NULL"`, not `CASCADE`. Deleting a step deletes its `TaskMessage` but leaves the
`ContextAugmentation` row permanently orphaned — an unbounded leak across every deleted or replanned
step. Separately, `Segmentation.volume_series_instance_uid` has no `ForeignKey` declared in the ORM at
all, although the database has one with `ON DELETE CASCADE` — this is the ORM-side reason D-17's
cascade behavior surprises the application code.

**Fix:**
```python
# db/models/Taskmessage.py
context_augmentation = relationship(
    "ContextAugmentation", cascade="all, delete-orphan", uselist=False,
)
```
```python
# db/models/segmentation.py
volume_series_instance_uid = Column(
    String, ForeignKey("volume_series.volume_series_instance_uid", ondelete="CASCADE"),
    nullable=False,
)
```
Then run a one-time cleanup for existing orphans:
```sql
DELETE FROM context_augmentations
WHERE id NOT IN (SELECT context_augmentation_id FROM task_messages WHERE context_augmentation_id IS NOT NULL);
```

---

## Operations & security

### D-29 — `deploy/.env` tracked with live credentials

**Severity:** Critical · **Evidence:** `[Confirmed]` fact · `[Policy]` remediation
**Where:** `.gitignore:13`; `deploy/.env:11-21`; `docker-compose.backend.cpu.yml:232-236`

**What happens:** `.gitignore:13` reads `# .env  #TEMPORARY COMMENTED OUT FOR TESTING PURPOSES` —
`CLAUDE.md`'s own Gotchas section confirms this is intentional-but-risky, not accidental. `deploy/.env`
is tracked in git and contains `POSTGRES_PASSWORD=password` and the full `DATABASE_URL` with that
password embedded. `.env.example` duplicates the same live values rather than placeholders. Postgres
is also published on host port `5433`.

**Fix (requires a decision, hence `[Policy]`):** Untrack the file, rotate every credential it exposed,
and replace `.env.example` with placeholders:
```bash
git rm --cached deploy/.env
```
```gitignore
.env
deploy/.env
```
```
# deploy/.env.example
POSTGRES_PASSWORD=<change-me>
DATABASE_URL=postgresql+asyncpg://workflow_user:<change-me>@postgres:5432/workflow_db
```
This is a `CLAUDE.md`-documented trade-off (local-dev convenience vs. credential hygiene); flag it to
the team before changing `.gitignore`, since other checked-in tooling may assume `.env` is trackable.

---

### D-30 — No authentication on any PHI route

**Severity:** Critical · **Evidence:** `[Confirmed]` absence · `[Policy]` requirement
**Where:** `Backend/application_api/main.py:64-82`; `Backend/application_api/db/models/patient.py:15-17`;
`Backend/application_api/api/routes/sync.py:123-177,195-218`

**What happens:** `patients.name`, `.birth_date`, `.gender` are stored in the clear with no
encryption, pseudonymisation, or audit trail, and none of the 16 routers registered in `main.py`
declare an authentication dependency. `POST /sync/orthanc-webhook` is reachable without
authentication and accepts `change_type: "deleted"` with an attacker-supplied `resource_id`, which
deletes rows straight from the request body.

**Fix (requires a decision on auth strategy, hence `[Policy]`):** At minimum, gate the webhook with a
shared secret header validated against an env var, and add a FastAPI dependency for the PHI routers:
```python
# sync.py
EXPECTED_WEBHOOK_TOKEN = os.environ["ORTHANC_WEBHOOK_TOKEN"]

async def verify_webhook_token(x_webhook_token: str = Header(...)):
    if not secrets.compare_digest(x_webhook_token, EXPECTED_WEBHOOK_TOKEN):
        raise HTTPException(status_code=401)

router = APIRouter(prefix="/sync", tags=["orthanc"], dependencies=[Depends(verify_webhook_token)])
```
Set the corresponding `Content-Type`/custom header in `on-stable-series.lua`'s `JSON_HEADERS`. Full
PHI-route authentication is a larger design decision (API keys, OAuth, session cookies) that should be
scoped as its own task rather than folded into this fix.

---

### D-31 — Release-candidate PostgreSQL holds clinical data

**Severity:** High · **Evidence:** `[Confirmed]` fact · `[Policy]` upgrade path
**Where:** `deploy/docker-compose.backend.cpu.yml:227`

**What happens:** Both compose files pin `postgres:18rc1-alpine3.22` — a release candidate, not a
generally-available release — for a database holding clinical imaging metadata. `CLAUDE.md` itself
lists the intended version as "PostgreSQL 18", but a release candidate specifically is not appropriate
for anything beyond development.

**Fix:**
```yaml
postgres:
  image: postgres:18-alpine  # or whatever GA patch version is current at fix time
```
Test the migration chain against the GA image in a disposable environment before switching the shared
compose file, since RC→GA can carry minor behavioral changes.

---

### D-32 — CPU/GPU stacks use different data volumes

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `deploy/docker-compose.backend.cpu.yml:271`; `deploy/docker-compose.backend.gpu.yml:292-293`

**What happens:** The CPU compose file declares `postgres-db` and `orthanc-db`; the GPU file declares
`postgres-db2` and `orthanc-db2`. Switching between CPU and GPU profiles on the same host silently
presents an empty database and an empty PACS — and since the fresh volume then replays the entire
Alembic chain, D-1's `TRUNCATE` migrations run against it too (harmlessly, since it's already empty,
but the demo-data migration D-9 also fires).

**Fix:** Use one volume name across both compose files:
```yaml
# both cpu.yml and gpu.yml
volumes:
  postgres-db:
  orthanc-db:
```
If CPU and GPU deployments are genuinely meant to have independent data (e.g. separate environments),
document that explicitly instead of leaving it as an unlabeled naming difference.

---

### D-33 — Migration image and runtime image are different code

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `deploy/docker-compose.backend.cpu.yml:55-92,132-135`;
`scripts/launch.backend.ps1:175-182`; `deploy/docker-compose.backend.gpu.yml:58-60`

**What happens:** `db-migrate` and `db-seed` have no volume mounts and run the code baked into the
image at build time. `application-api` bind-mounts the host's `Backend/application_api` directory over
that same image path, so it runs host source instead. If a model is edited without a corresponding
migration, the running API sees the new ORM shape while the database is still on the old migration —
within a single `docker compose up`. Compounding this, both launchers run
`docker compose up --pull never` **without `--build`**, which defeats the very
`pull_policy: build` setting the GPU compose file's own comment explains is there specifically to
avoid stale migration images.

**Fix:** Either mount the same source into `db-migrate`/`db-seed` as `application-api` does, or (safer)
stop bind-mounting source into `application-api` in anything other than the explicit `backend.dev`
overlay, so the three services are guaranteed to run identical code:
```yaml
# docker-compose.backend.cpu.yml — remove from the base `application-api` service:
#   - ${COMPOSE_PROJECT_DIR}/Backend/application_api:/app/application_api
# and add it only in docker-compose.backend.dev.yml
```
And add `--build` (or an explicit build step) to both launchers:
```powershell
docker compose --env-file deploy/.env -f deploy/docker-compose.backend.cpu.yml up --build --wait
```

---

## Testing & documentation

### D-34 — No DB test infrastructure; 5 contract tests broken

**Severity:** High · **Evidence:** `[Confirmed]`
**Where:** `Backend/tests/contracts/test_chat_pagination_contract.py`;
`Backend/tests/contracts/test_startup_migration_contract.py`;
`Backend/tests/contracts/test_service_route_ownership.py:74`;
`cubeai-docs/database/README.md`; `cubeai-docs/api-reference/README.md`

**What happens:** There is no database fixture in `Backend/conftest.py`, no sqlite/testcontainers
setup, and no transactional-rollback fixture — nothing in `Backend/tests/` exercises
`orthanc_sync.py`, the migration chain, or any of the delete paths above, which is why the findings
above went undetected. Concretely, **5 test functions are currently broken** by the
`application_api` refactor: 3 in `test_chat_pagination_contract.py` (lines 7-8, 20-21, 29-30) and 2 in
`test_startup_migration_contract.py` (lines 7-16, 19-27) read paths (`Backend/monailabel/endpoints/`,
`Backend/monailabel/crud/`, `Backend/migrations/versions/`, `Backend/monailabel/models/__init__.py`)
that no longer exist post-refactor, raising `FileNotFoundError`. Worse, the two files **contradict**
each other: `test_startup_migration_contract.py:8-13` reads `Backend/migrations/versions/...` while
`test_service_route_ownership.py:74` explicitly asserts that this directory must *not* exist. Whichever
one is correct, the migration-integrity guard is currently dead either way. Separately,
`cubeai-docs/database/README.md` documents tables (`list_workflow`, `research_sessions`,
`session_studies`, `agent_outputs`) that exist in no model or migration, and never mentions
`patients`/`studies`/`volume_series`/`segmentations` — the entire Orthanc-derived schema is
undocumented — and `cubeai-docs/api-reference/README.md` omits `/sync/*` entirely.

**Fix:** Update the paths in the two broken contract test files to the current `application_api`
layout, or delete them and write replacements against real paths:
```python
# test_startup_migration_contract.py
migration = (
    BACKEND_ROOT / "application_api" / "migrations" / "versions"
    / "f1a2b3c4d5e6_add_application_api_indexes.py"
).read_text(encoding="utf-8")
```
Add a real DB test fixture (respecting the existing test-isolation rule in `CLAUDE.md` that unit tests
must not contact a live DB — this belongs in the `integration` suite):
```python
@pytest.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()
    await engine.dispose()
```
Rewrite `cubeai-docs/database/README.md` to describe the schema that actually exists (the six tables
above plus their relationships) and add a `/sync/*` section to the API reference.

---

## Appendix A — PACS & ingress (secondary scope)

These surfaced during the audit and are named because Orthanc is the database's write path, but they
are pure PACS/ingress concerns rather than database defects — kept brief and out of the main severity
count.

- **P-1** `[Confirmed/Policy]` — Orthanc runs `RemoteAccessAllowed:true` + `AuthenticationEnabled:false`
  in `Backend/orthanc/orthanc.json`, and nginx's `location /pacs/` in
  `Frontend/OHIF-Viewer/platform/app/.recipes/OpenResty-Orthanc/config/nginx.conf` proxies the entire
  Orthanc REST API, not just DICOMweb.
- **P-2** `[Confirmed]` — `deploy/docker-compose.backend.prod.yml`, which unpublishes every backend
  port, is never invoked by either launcher script (`scripts/launch.ps1`,
  `scripts/launch.backend.ps1`).
- **P-3** `[Confirmed]` — Orthanc's `RegisteredUsers` is `{"test": "test"}` while every client sends
  `orthanc:orthanc`, and the agent system / MONAI Label clients send no credentials at all — enabling
  auth without fixing those clients breaks the segmentation pipeline.
- **P-4** `[Confirmed]` — the shipped `docker_openresty-orthanc.js` viewer config hardcodes
  `http://127.0.0.1/pacs/dicom-web`, which only works when the browser runs on the server host.
- **P-5** `[Confirmed]` — `/pacs/` in `nginx.conf` sets no `client_max_body_size`, so STOW uploads hit
  nginx's 1 MB default; contrast the `/inference/` block, which correctly sets `2g`.
- **P-6** `[Confirmed]` — `OverwriteInstances:false` combined with deterministic SEG UIDs means a
  re-upload returns `"Status":"AlreadyStored"`, and `Backend/agent_system/agents/execution_agents/segmentation/services.py`
  never inspects that field, so a changed mask can silently fail to update.
- **P-7** `[Confirmed design / Needs repro throughput]` — Orthanc's default SQLite index under
  `HttpThreadsCount:50` with `StorageAccessOnFind:"Always"`.
- **P-8** `[Confirmed]` — no healthcheck is defined for the Orthanc service in either compose file;
  every dependant uses `condition: service_started`, which only confirms the container process exists.

---

## Remediation phases

### Phase 0 — Establish a recoverable baseline *(blocking; nothing else starts until this passes)*

1. Snapshot both the PostgreSQL and Orthanc Docker volumes.
2. Produce a logical `pg_dump` of the application database.
3. Restore that dump into a disposable environment and confirm it comes up.
4. Record row counts for `patients`, `studies`, `volume_series`, `segmentations`, `radiomics_features`.
5. Record the current `alembic_version` value(s).
6. Capture the live schema (`pg_dump --schema-only`, plus an `information_schema` query diffed against
   `Base.metadata`) — this is what resolves D-3's `[Needs repro]` half.
7. **Freeze new deployments** until D-1 is neutralised for every environment identified in step 5.

**Acceptance criteria:** a restore into a clean environment reproduces the recorded row counts and
Alembic revision exactly; the schema capture from step 6 is committed as the drift baseline for D-3's
reconciliation migration.

### Phases 1–9

| # | Phase | Findings | Acceptance criteria |
|---|---|---|---|
| 1 | Back up and restore-test | Phase 0 | Restore is automated and runs in CI on a schedule |
| 2 | Neutralise destructive migrations; fix volume identity | D-1, D-32 | Upgrading a snapshot taken from every historical `alembic_version` checkpoint preserves all clinical rows; switching CPU↔GPU profiles preserves the same data |
| 3 | Alembic as sole schema authority | D-2, D-8 | `create_all()` removed from the seeder; `alembic upgrade head` on an empty database produces zero drift against `Base.metadata` |
| 4 | Authentication and secrets | D-29, D-30 | Anonymous requests to PHI routes receive 401/403; no credential remains in a tracked file; every exposed credential is rotated |
| 5 | Schema reconciliation: constraints and indexes | D-3, D-4, D-5, D-6, D-26, D-28 | One reconciliation migration lands; the association table's PK and the `plans` unique constraint are restored; `UNIQUE(name, birth_date)` is dropped; the ORM/DDL diff from Phase 0 step 6 is empty afterward |
| 6 | Durable, idempotent sync | D-11–D-18 | Replaying one Orthanc change event 100 times produces exactly one row; concurrent study+series events for the same study yield exactly one patient/study/series; a webhook that fails once is eventually reconciled by the poller; a SEG arriving before its volume is retried rather than dropped |
| 7 | Replace raw workflow SQL/driver access | D-19, D-20, D-21, D-22, D-23 | `workflow.py` uses normal SQLAlchemy transactions; a missing workflow record returns 404, not 500; ten slow Orthanc operations in flight do not cause unrelated DB reads to fail |
| 8 | Migration, concurrency, restore and auth tests | D-25, D-33, D-34 | The 5 currently-broken contract tests pass (updated paths) or are replaced; a migration/concurrency/restore/auth test suite exists and runs in the `integration` gate; migration and runtime images are verified byte-identical in CI |
| 9 | Performance, observability, documentation | D-24, D-27, D-31, Appendix A | The N+1 in plan listing is removed; DICOM coercions preserve or explicitly reject rather than silently rewriting; PostgreSQL runs on a GA release; `cubeai-docs/database/README.md` and the API reference describe the schema and endpoints that actually exist |

---

## Verification

Commands that settle the load-bearing claims above; run before treating any `[Needs repro]` finding
as resolved or any `[Confirmed]` finding as stale:

```bash
git ls-files deploy/.env                                                          # D-29
grep -rn "TRUNCATE" Backend/application_api/migrations/versions/                  # D-1
grep -n "create_all" Backend/application_api/seeders/model.py                     # D-2
alembic -c Backend/application_api/alembic.ini check                             # D-3, D-8
alembic -c Backend/application_api/alembic.ini heads                             # D-25 (confirms single head today)
docker compose -f deploy/docker-compose.backend.cpu.yml config --volumes
docker compose -f deploy/docker-compose.backend.gpu.yml config --volumes         # D-32 (diff the two)
grep -rn "backend.prod.yml" scripts/                                             # P-2 (expect no matches)
grep -n "client_max_body_size" \
  Frontend/OHIF-Viewer/platform/app/.recipes/OpenResty-Orthanc/config/nginx.conf # P-5
python -m pytest Backend/tests/contracts -q                                      # D-34 (expect exactly 5 failures)
```

Findings still labelled `[Needs repro]` after Phase 0 and the concurrency tests in Phase 6 — D-14's
data loss, D-19's exhaustion, D-20's corruption — should be re-labelled `[Confirmed]` or downgraded to
"not observed" once their stated reproduction procedure has actually been run, not before.
