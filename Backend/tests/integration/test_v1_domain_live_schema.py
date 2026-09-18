"""Live constraint checks for database-backed domain tables."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 is required for live checks")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "application_api" / "alembic.ini"
LIVE_DSN = os.environ.get("V1_LIVE_DATABASE_URL")

pytestmark.append(
    pytest.mark.skipif(
        not LIVE_DSN,
        reason="set V1_LIVE_DATABASE_URL to a disposable PostgreSQL 18 database",
    )
)

AGENTS = (
    "study_population_planner_agent",
    "segmentation_agent",
    "feature_extraction_agent",
    "modeling_agent",
    "result_reporter_agent",
    "latex_reporter_agent",
)


def _alembic(command: str, revision: str) -> str:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = LIVE_DSN
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), command, revision],
        cwd=str(BACKEND_ROOT),
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"alembic {command} {revision} failed:\n{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout + completed.stderr


def _connect():
    connection = psycopg2.connect(LIVE_DSN)
    connection.autocommit = False
    return connection


def _execute(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone() if cursor.description else None
    connection.commit()
    return row[0] if row else None


def _scalar(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    connection.rollback()
    return row[0] if row else None


def _rejects(connection, sql, params=()) -> str:
    """Run a statement that must fail; return the database's message."""
    with pytest.raises(psycopg2.Error) as excinfo:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
        connection.commit()
    connection.rollback()
    return str(excinfo.value)


class World:
    """A synthetic archive and two approved sessions."""

    def __init__(self, connection):
        self.connection = connection
        self.tag = uuid.uuid4().hex[:8]
        self.volumes: list[tuple[str, str, str]] = []  # (study, volume, seg)
        self.sessions: dict[str, dict[str, str]] = {}

    def archive(self, count: int = 4) -> None:
        with self.connection.cursor() as cursor:
            for index in range(count):
                patient = str(uuid.uuid4())
                study = f"1.2.{self.tag}.{index}"
                volume = f"1.3.{self.tag}.{index}"
                seg = f"1.4.{self.tag}.{index}"
                cursor.execute(
                    "INSERT INTO patients (patient_id, orthanc_patient_id, name, archive_status) "
                    "VALUES (%s, %s, %s, 'available')",
                    (patient, f"p-{self.tag}-{index}", f"Synthetic {index}"),
                )
                cursor.execute(
                    "INSERT INTO studies (study_uid, orthanc_study_id, patient_id, archive_status) "
                    "VALUES (%s, %s, %s, 'available')",
                    (study, f"s-{self.tag}-{index}", patient),
                )
                cursor.execute(
                    "INSERT INTO volume_series (volume_series_instance_uid, orthanc_series_id, "
                    "study_uid, num_slices, modality, series_description, archive_status) "
                    "VALUES (%s, %s, %s, 12, 'CT', 'abdomen', 'available')",
                    (volume, f"v-{self.tag}-{index}", study),
                )
                cursor.execute(
                    "INSERT INTO segmentations (label_series_instance_uid, orthanc_series_id, "
                    "volume_series_instance_uid, series_description, origin, archive_status) "
                    "VALUES (%s, %s, %s, 'seg', 'pipeline', 'available')",
                    (seg, f"g-{self.tag}-{index}", volume),
                )
                self.volumes.append((study, volume, seg))
        self.connection.commit()

    def approved_session(self, name: str) -> dict[str, str]:
        session_id = str(uuid.uuid4())
        runs: dict[str, str] = {"session_id": session_id}
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sessions (session_id, title, prompt, prompt_kind, status) "
                "VALUES (%s, %s, 'synthetic prompt', 'general', 'ready')",
                (session_id, name),
            )
            cursor.execute(
                "INSERT INTO plans (session_id, approved_at, updated_at) VALUES (%s, NULL, now())",
                (session_id,),
            )
            steps = []
            for order, agent in enumerate(AGENTS, start=1):
                step_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO plan_steps (step_id, session_id, step_order, agent_type, title, "
                    "instructions) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)",
                    (step_id, session_id, order, agent, agent),
                )
                steps.append((agent, step_id))
        self.connection.commit()
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE plans SET approved_at = now() WHERE session_id = %s", (session_id,))
            for agent, step_id in steps:
                cursor.execute(
                    "INSERT INTO agent_runs (step_id, session_id, status) "
                    "VALUES (%s, %s, 'pending') RETURNING agent_run_id",
                    (step_id, session_id),
                )
                runs[agent] = str(cursor.fetchone()[0])
        self.connection.commit()
        self.sessions[name] = runs
        return runs

    def cohort(self, run_id: str, *, key: str = "population") -> str:
        return str(
            _execute(
                self.connection,
                "INSERT INTO cohorts (created_by_agent_run_id, cohort_key, criteria, label_definition) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb) RETURNING cohort_id",
                (
                    run_id,
                    key,
                    json.dumps({"schema_version": 1, "labelling_mode": "manual"}),
                    json.dumps(
                        {"schema_version": 1, "kind": "binary", "field": "has_target_disease"}
                    ),
                ),
            )
        )

    def member(self, cohort_id: str, index: int, ordinal: int, label: bool | None = True) -> str:
        study, volume, _ = self.volumes[index]
        return str(
            _execute(
                self.connection,
                "INSERT INTO cohort_members (cohort_id, study_uid, volume_series_instance_uid, "
                "subject_key, ordinal, label, label_source) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) "
                "RETURNING member_id",
                (
                    cohort_id,
                    study,
                    volume,
                    f"subject-{index}",
                    ordinal,
                    None if label is None else json.dumps({"has_target_disease": label}),
                    "pending" if label is None else "manual_override",
                ),
            )
        )


@pytest.fixture(scope="module")
def world():
    connection = _connect()
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    connection.commit()
    connection.close()
    _alembic("upgrade", "head")

    connection = _connect()
    state = World(connection)
    state.archive(4)
    state.approved_session("A")
    state.approved_session("B")
    try:
        yield state
    finally:
        connection.close()


# ----------------------------------------------------------------------
# Step H — cohorts
# ----------------------------------------------------------------------
def test_members_align_to_the_archive_and_labels_follow_the_definition(world):
    c = world.connection
    cohort = world.cohort(world.sessions["A"]["study_population_planner_agent"])
    world.member(cohort, 0, 1, True)

    # A study/volume pair the archive does not hold is rejected (composite FK):
    # the volume is real and the study is real, but not of each other.
    study_other, _, _ = world.volumes[1]
    _, volume_three, _ = world.volumes[3]
    message = _rejects(
        c,
        "INSERT INTO cohort_members (cohort_id, study_uid, volume_series_instance_uid, subject_key, "
        "ordinal) VALUES (%s, %s, %s, 's', 9)",
        (cohort, study_other, volume_three),
    )
    assert "fk_cohort_members_study_uid_volume_series" in message

    # A binary label must be a boolean of the defined field.
    message = _rejects(
        c,
        "UPDATE cohort_members SET label = '{\"has_target_disease\": \"yes\"}'::jsonb, "
        "label_source = 'manual_override' WHERE cohort_id = %s",
        (cohort,),
    )
    assert "boolean" in message

    # A pending label carries no value.
    message = _rejects(
        c,
        "UPDATE cohort_members SET label_source = 'pending' WHERE cohort_id = %s",
        (cohort,),
    )
    assert "pending_has_no_label" in message


def test_a_frozen_cohort_and_its_members_are_immutable(world):
    c = world.connection
    cohort = world.cohort(world.sessions["A"]["study_population_planner_agent"], key="frozen")
    member = world.member(cohort, 1, 1, False)
    _execute(c, "UPDATE cohorts SET frozen_at = now() WHERE cohort_id = %s", (cohort,))

    assert "frozen" in _rejects(
        c, "UPDATE cohort_members SET ordinal = 2 WHERE member_id = %s", (member,)
    )
    assert "frozen" in _rejects(c, "DELETE FROM cohort_members WHERE member_id = %s", (member,))
    assert "frozen" in _rejects(
        c,
        "UPDATE cohorts SET criteria = '{\"schema_version\": 1, \"x\": 1}'::jsonb WHERE cohort_id = %s",
        (cohort,),
    )
    assert "frozen" in _rejects(c, "UPDATE cohorts SET frozen_at = NULL WHERE cohort_id = %s", (cohort,))
    assert "cannot be deleted" in _rejects(c, "DELETE FROM cohorts WHERE cohort_id = %s", (cohort,))


# ----------------------------------------------------------------------
# Step H — segmentation review
# ----------------------------------------------------------------------
def _batch(world, session: str, cohort: str, key: str) -> str:
    return str(
        _execute(
            world.connection,
            "INSERT INTO segmentation_runs (created_by_agent_run_id, cohort_id, batch_key, config) "
            "VALUES (%s, %s, %s, '{\"schema_version\": 1}'::jsonb) RETURNING segmentation_run_id",
            (world.sessions[session]["segmentation_agent"], cohort, key),
        )
    )


def _target(world, run: str, key: str = "spleen") -> str:
    return str(
        _execute(
            world.connection,
            "INSERT INTO segmentation_targets (segmentation_run_id, target_key, label, specification) "
            "VALUES (%s, %s, 'spleen', '{\"schema_version\": 1}'::jsonb) RETURNING target_id",
            (run, key),
        )
    )


def test_a_segmentation_run_needs_a_cohort_from_its_own_session_and_freezes_it(world):
    c = world.connection
    assert "null value" in _rejects(
        c,
        "INSERT INTO segmentation_runs (created_by_agent_run_id, batch_key, config) "
        "VALUES (%s, 'x', '{\"schema_version\": 1}'::jsonb)",
        (world.sessions["A"]["segmentation_agent"],),
    )

    cohort_a = world.cohort(world.sessions["A"]["study_population_planner_agent"], key="cross")
    message = _rejects(
        c,
        "INSERT INTO segmentation_runs (created_by_agent_run_id, cohort_id, batch_key, config) "
        "VALUES (%s, %s, 'x', '{\"schema_version\": 1}'::jsonb)",
        (world.sessions["B"]["segmentation_agent"], cohort_a),
    )
    assert "R1" in message

    assert _scalar(c, "SELECT frozen_at FROM cohorts WHERE cohort_id = %s", (cohort_a,)) is None
    _batch(world, "A", cohort_a, "freezes")
    assert _scalar(c, "SELECT frozen_at FROM cohorts WHERE cohort_id = %s", (cohort_a,)) is not None


def test_pairs_are_aligned_by_composite_keys(world):
    c = world.connection
    runs = world.sessions["A"]
    cohort_1 = world.cohort(runs["study_population_planner_agent"], key="align-1")
    member_1 = world.member(cohort_1, 0, 1)
    member_1b = world.member(cohort_1, 1, 2)
    cohort_2 = world.cohort(runs["study_population_planner_agent"], key="align-2")
    member_2 = world.member(cohort_2, 2, 1)

    batch_1 = _batch(world, "A", cohort_1, "align-1")
    target_1 = _target(world, batch_1)
    batch_2 = _batch(world, "A", cohort_2, "align-2")
    target_2 = _target(world, batch_2)
    _, volume_0, seg_0 = world.volumes[0]
    _, volume_1, seg_1 = world.volumes[1]
    _, volume_2, _ = world.volumes[2]

    insert = (
        "INSERT INTO segmentation_pairs (segmentation_run_id, target_id, volume_series_instance_uid, "
        "cohort_id, cohort_member_id, candidate_segmentation_uid) VALUES (%s, %s, %s, %s, %s, %s)"
    )
    # A pair whose cohort differs from its batch's cohort.
    assert "fk_segmentation_pairs_segmentation_run_id_cohort_id" in _rejects(
        c, insert, (batch_1, target_1, volume_2, cohort_2, member_2, None)
    )
    # A target from another batch.
    assert "fk_segmentation_pairs_segmentation_run_id_target_id" in _rejects(
        c, insert, (batch_1, target_2, volume_0, cohort_1, member_1, None)
    )
    # A pair whose volume is not its member's volume.
    assert "fk_segmentation_pairs_cohort_member" in _rejects(
        c, insert, (batch_1, target_1, volume_1, cohort_1, member_1, None)
    )
    # A member from the wrong cohort.
    assert "fk_segmentation_pairs_cohort_member" in _rejects(
        c, insert, (batch_1, target_1, volume_2, cohort_1, member_2, None)
    )
    # A candidate SEG of another volume.
    assert "fk_segmentation_pairs_candidate_segmentation" in _rejects(
        c, insert, (batch_1, target_1, volume_0, cohort_1, member_1, seg_1)
    )
    # The aligned pair commits; the natural key refuses a duplicate.
    _execute(c, insert, (batch_1, target_1, volume_0, cohort_1, member_1, seg_0))
    assert "uq_segmentation_pairs_target_id" in _rejects(
        c, insert, (batch_1, target_1, volume_0, cohort_1, member_1, seg_0)
    )
    _execute(c, insert, (batch_1, target_1, volume_1, cohort_1, member_1b, None))


def test_a_submitted_review_is_consistent_and_final(world):
    c = world.connection
    runs = world.sessions["A"]
    cohort = world.cohort(runs["study_population_planner_agent"], key="review")
    member_0 = world.member(cohort, 0, 1)
    member_1 = world.member(cohort, 1, 2)
    batch = _batch(world, "A", cohort, "review")
    target = _target(world, batch)
    _, volume_0, seg_0 = world.volumes[0]
    _, volume_1, seg_1 = world.volumes[1]
    for volume, member, seg in ((volume_0, member_0, seg_0), (volume_1, member_1, seg_1)):
        _execute(
            c,
            "INSERT INTO segmentation_pairs (segmentation_run_id, target_id, volume_series_instance_uid, "
            "cohort_id, cohort_member_id, candidate_segmentation_uid, status, external_job_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'ready', %s)",
            (batch, target, volume, cohort, member, seg, f"job-{volume}"),
        )

    # The MONAI receipt pointer is recorded once, never re-pointed.
    assert "reconciles against MONAI job" in _rejects(
        c,
        "UPDATE segmentation_pairs SET external_job_id = 'other' WHERE volume_series_instance_uid = %s",
        (volume_0,),
    )
    # Rejected requires no selected output.
    assert "selected_implies_positive_decision" in _rejects(
        c,
        "UPDATE segmentation_pairs SET review_decision = 'rejected', selected_segmentation_uid = %s "
        "WHERE volume_series_instance_uid = %s",
        (seg_0, volume_0),
    )

    _execute(c, "UPDATE segmentation_runs SET review_status = 'pending' WHERE segmentation_run_id = %s", (batch,))
    _execute(
        c,
        "UPDATE segmentation_runs SET review_status = 'submitting', review_request_hash = %s "
        "WHERE segmentation_run_id = %s",
        ("a" * 64, batch),
    )
    # A ready pair with no decision at all cannot be submitted (the pair for
    # volume_1 has none yet).
    assert "lack a final decision" in _rejects(
        c,
        "UPDATE segmentation_runs SET review_status = 'submitted', reviewed_at = now() "
        "WHERE segmentation_run_id = %s",
        (batch,),
    )
    _execute(
        c,
        "UPDATE segmentation_pairs SET review_decision = 'accepted', selected_segmentation_uid = "
        "candidate_segmentation_uid WHERE volume_series_instance_uid = %s",
        (volume_0,),
    )
    assert "lack a final decision" in _rejects(
        c,
        "UPDATE segmentation_runs SET review_status = 'submitted', reviewed_at = now() "
        "WHERE segmentation_run_id = %s",
        (batch,),
    )
    # A modified decision must name the SEG it selected.
    _execute(
        c,
        "UPDATE segmentation_pairs SET review_decision = 'modified' WHERE volume_series_instance_uid = %s",
        (volume_1,),
    )
    assert "lack a final decision" in _rejects(
        c,
        "UPDATE segmentation_runs SET review_status = 'submitted', reviewed_at = now() "
        "WHERE segmentation_run_id = %s",
        (batch,),
    )
    _execute(
        c,
        "UPDATE segmentation_pairs SET review_decision = 'rejected' WHERE volume_series_instance_uid = %s",
        (volume_1,),
    )
    _execute(
        c,
        "UPDATE segmentation_runs SET review_status = 'submitted', reviewed_at = now() "
        "WHERE segmentation_run_id = %s",
        (batch,),
    )

    # Final: neither the batch nor a pair changes, and nothing is deleted.
    assert "submitted" in _rejects(
        c,
        "UPDATE segmentation_pairs SET review_decision = 'accepted' WHERE volume_series_instance_uid = %s "
        "AND segmentation_run_id = %s",
        (volume_1, batch),
    )
    assert "submitted and final" in _rejects(
        c,
        "UPDATE segmentation_runs SET review_status = 'pending', review_request_hash = NULL, "
        "reviewed_at = NULL WHERE segmentation_run_id = %s",
        (batch,),
    )
    assert "cannot be deleted" in _rejects(
        c, "DELETE FROM segmentation_pairs WHERE segmentation_run_id = %s", (batch,)
    )


def test_deleting_the_archive_row_under_a_review_is_restricted(world):
    """RESTRICT everywhere: Orthanc loss marks rows unavailable, it never
    deletes the review that referenced them."""
    c = world.connection
    _, _, seg_0 = world.volumes[0]
    assert "segmentation_pairs" in _rejects(
        c, "DELETE FROM segmentations WHERE label_series_instance_uid = %s", (seg_0,)
    )


# ----------------------------------------------------------------------
# Step I — radiomics features and feature sets
# ----------------------------------------------------------------------
def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


_RADIOMICS_INSERT = (
    "INSERT INTO radiomics_features (session_id, label_series_instance_uid, source_volume_uid, "
    "segment_number, created_by_agent_run_id, features, extractor_name, extractor_version, "
    "configuration, config_digest, image_digest, segmentation_digest, source_digest, "
    "schema_version) VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pyradiomics', '3.1.0', "
    "'{\"schema_version\": 1}'::jsonb, %s, %s, %s, %s, 1) RETURNING id"
)


def _radiomics_params(world, session, index, *, seed="x", agent="feature_extraction_agent",
                      volume_index=None, run=True):
    runs = world.sessions[session]
    _, volume, seg = world.volumes[index]
    if volume_index is not None:
        volume = world.volumes[volume_index][1]
    return (
        runs["session_id"],
        seg,
        volume,
        1,
        runs[agent] if run else None,
        json.dumps({"original_firstorder_Mean": 1.5, "original_shape_VoxelVolume": 12.0}),
        _digest(f"config-{seed}"),
        _digest(f"image-{index}"),
        _digest(f"segment-{index}"),
        _digest(f"source-{index}-{seed}"),
    )


def test_radiomics_rows_are_content_addressed_owned_and_immutable(world):
    c = world.connection
    row = str(_execute(c, _RADIOMICS_INSERT, _radiomics_params(world, "A", 0, seed="first")))

    # A replay collides on the content-addressed key instead of duplicating.
    assert "uq_radiomics_features_session_id" in _rejects(
        c, _RADIOMICS_INSERT, _radiomics_params(world, "A", 0, seed="first")
    )
    # A changed configuration is a different row, never the cached earlier one.
    _execute(c, _RADIOMICS_INSERT, _radiomics_params(world, "A", 0, seed="wider-bins"))

    # Immutable once written; research history is never deleted.
    assert "immutable" in _rejects(
        c, "UPDATE radiomics_features SET extractor_version = 'x' WHERE id = %s", (row,)
    )
    assert "cannot be deleted" in _rejects(c, "DELETE FROM radiomics_features WHERE id = %s", (row,))

    # A new row without full provenance is rejected...
    params = list(_radiomics_params(world, "A", 1, seed="no-digest"))
    params[9] = None
    assert "not-null" in _rejects(c, _RADIOMICS_INSERT, tuple(params))
    # ... but a NULL created_by_agent_run_id is not.
    _execute(c, _RADIOMICS_INSERT, _radiomics_params(world, "A", 1, seed="unattributed", run=False))

    # The measured SEG must be a mask of the named source volume.
    assert "fk_radiomics_features_segmentation" in _rejects(
        c, _RADIOMICS_INSERT, _radiomics_params(world, "A", 2, seed="wrong-volume", volume_index=3)
    )
    # A run from another session cannot produce this session's row.
    params = list(_radiomics_params(world, "A", 2, seed="cross"))
    params[4] = world.sessions["B"]["feature_extraction_agent"]
    assert "fk_radiomics_features_created_by_agent_run_id_session_id" in _rejects(
        c, _RADIOMICS_INSERT, tuple(params)
    )
    # The modeling agent cannot write radiomics rows.
    assert "D17" in _rejects(
        c, _RADIOMICS_INSERT, _radiomics_params(world, "A", 2, seed="modeling", agent="modeling_agent")
    )


_FEATURE_SET_INSERT = (
    "INSERT INTO feature_sets (created_by_agent_run_id, cohort_id, set_key, feature_names, "
    "feature_matrix, source_feature_ids, selection, quality_summary, config_digest, "
    "source_digest, manifest_digest) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, "
    "'{\"schema_version\": 1, \"label_supervised_on_full_data\": false, "
    "\"holdout_groups\": []}'::jsonb, %s::jsonb, %s, %s, %s) RETURNING feature_set_id"
)


def _feature_set_params(world, session, cohort, key, names, matrix, *, sources=None,
                        summary=None, agent="feature_extraction_agent"):
    return (
        world.sessions[session][agent],
        cohort,
        key,
        json.dumps(names),
        json.dumps(matrix),
        None if sources is None else json.dumps(sources),
        json.dumps(
            summary
            or {"schema_version": 1, "subjects": len(matrix), "features": len(names),
                "missing_values": {"count": 0}}
        ),
        _digest(f"fs-config-{key}"),
        _digest(f"fs-source-{key}"),
        _digest(f"fs-manifest-{key}"),
    )


def test_a_feature_set_is_rectangular_same_session_and_owned(world):
    c = world.connection
    runs = world.sessions["A"]
    cohort = world.cohort(runs["study_population_planner_agent"], key="features-shape")
    world.member(cohort, 0, 1, True)
    world.member(cohort, 1, 2, False)
    other = world.cohort(world.sessions["B"]["study_population_planner_agent"], key="features-b")
    vol0, vol1 = world.volumes[0][1], world.volumes[1][1]

    # A feature set cannot exist without a cohort at all.
    assert "null value" in _rejects(
        c,
        "INSERT INTO feature_sets (created_by_agent_run_id, set_key, feature_names, feature_matrix, "
        "selection, config_digest, source_digest, manifest_digest) VALUES (%s, 'x', '[\"a\"]'::jsonb, "
        "'{}'::jsonb, '{\"schema_version\": 1, \"label_supervised_on_full_data\": false}'::jsonb, "
        "%s, %s, %s)",
        (runs["feature_extraction_agent"], _digest("1"), _digest("2"), _digest("3")),
    )
    # A cohort from another session.
    assert "R1" in _rejects(
        c, _FEATURE_SET_INSERT,
        _feature_set_params(world, "A", other, "cross", ["a"], {vol0: [1.0]}),
    )
    # Modeling cannot write feature sets.
    assert "D17" in _rejects(
        c, _FEATURE_SET_INSERT,
        _feature_set_params(world, "A", cohort, "modeling", ["a"], {vol0: [1.0]},
                            agent="modeling_agent"),
    )
    # Rule 1: a 2-name / 1-value row does not commit.
    assert "feature_matrix_shape" in _rejects(
        c, _FEATURE_SET_INSERT,
        _feature_set_params(world, "A", cohort, "ragged", ["a", "b"], {vol0: [1.0]}),
    )

    assert _scalar(c, "SELECT frozen_at FROM cohorts WHERE cohort_id = %s", (cohort,)) is None
    _execute(
        c, _FEATURE_SET_INSERT,
        _feature_set_params(world, "A", cohort, "valid", ["a", "b"],
                            {vol0: [1.0, 2.0], vol1: [3.0, 4.0]}),
    )
    # Freeze on first downstream use.
    assert _scalar(c, "SELECT frozen_at FROM cohorts WHERE cohort_id = %s", (cohort,)) is not None


def test_freezing_a_feature_set_verifies_what_a_check_cannot(world):
    c = world.connection
    runs = world.sessions["A"]
    cohort = world.cohort(runs["study_population_planner_agent"], key="features-freeze")
    world.member(cohort, 0, 1, True)
    world.member(cohort, 1, 2, False)
    vol0, vol1 = world.volumes[0][1], world.volumes[1][1]
    source_a = str(_execute(c, _RADIOMICS_INSERT, _radiomics_params(world, "A", 0, seed="freeze")))
    source_b = str(_execute(c, _RADIOMICS_INSERT, _radiomics_params(world, "B", 0, seed="freeze")))

    feature_set = str(
        _execute(
            c, _FEATURE_SET_INSERT,
            _feature_set_params(
                world, "A", cohort, "freeze", ["a", "b"],
                {vol0: [1.0, 2.0], vol1: [3.0, None]},
                sources=[source_a],
                summary={"schema_version": 1, "subjects": 2, "features": 2,
                         "missing_values": {"count": 1}},
            ),
        )
    )
    freeze = "UPDATE feature_sets SET frozen_at = now() WHERE feature_set_id = %s"

    # A missing value without a recorded reason.
    assert "missing value" in _rejects(c, freeze, (feature_set,))
    _execute(
        c,
        "UPDATE feature_sets SET quality_summary = jsonb_set(quality_summary, "
        "'{missing_values,reason}', '\"not measured\"') WHERE feature_set_id = %s",
        (feature_set,),
    )
    # A key that is not a volume of this cohort.
    _execute(
        c,
        "UPDATE feature_sets SET feature_matrix = feature_matrix || '{\"stranger\": [0, 0]}'::jsonb, "
        "quality_summary = jsonb_set(quality_summary, '{subjects}', '3') WHERE feature_set_id = %s",
        (feature_set,),
    )
    assert "not volume UIDs" in _rejects(c, freeze, (feature_set,))
    _execute(
        c,
        "UPDATE feature_sets SET feature_matrix = feature_matrix - 'stranger', "
        "quality_summary = jsonb_set(quality_summary, '{subjects}', '2') WHERE feature_set_id = %s",
        (feature_set,),
    )
    # Rule 3: the label never enters the matrix.
    _execute(
        c, "UPDATE feature_sets SET feature_names = '[\"a\", \"target\"]'::jsonb WHERE feature_set_id = %s",
        (feature_set,),
    )
    assert "never the label" in _rejects(c, freeze, (feature_set,))
    _execute(
        c, "UPDATE feature_sets SET feature_names = '[\"a\", \"b\"]'::jsonb WHERE feature_set_id = %s",
        (feature_set,),
    )
    # Duplicate and cross-session sources.
    _execute(
        c, "UPDATE feature_sets SET source_feature_ids = %s::jsonb WHERE feature_set_id = %s",
        (json.dumps([source_a, source_a]), feature_set),
    )
    assert "repeats" in _rejects(c, freeze, (feature_set,))
    _execute(
        c, "UPDATE feature_sets SET source_feature_ids = %s::jsonb WHERE feature_set_id = %s",
        (json.dumps([source_a, source_b]), feature_set),
    )
    assert "another session" in _rejects(c, freeze, (feature_set,))
    _execute(
        c, "UPDATE feature_sets SET source_feature_ids = %s::jsonb WHERE feature_set_id = %s",
        (json.dumps([source_a]), feature_set),
    )

    _execute(c, freeze, (feature_set,))
    assert "frozen and immutable" in _rejects(
        c, "UPDATE feature_sets SET analysis = '{\"schema_version\": 1}'::jsonb WHERE feature_set_id = %s",
        (feature_set,),
    )
    assert "cannot be deleted" in _rejects(
        c, "DELETE FROM feature_sets WHERE feature_set_id = %s", (feature_set,)
    )
    # The measurements behind a frozen set are preserved: a SEG under it
    # cannot be deleted out from under the radiomics row (RESTRICT).
    assert "radiomics_features" in _rejects(
        c, "DELETE FROM segmentations WHERE label_series_instance_uid = %s", (world.volumes[0][2],)
    )


def test_legacy_radiomics_rows_keep_null_provenance_and_never_enter_a_frozen_set(world):
    """end to end: a row that pre-dates this migration survives the upgrade with NULL provenance, new rows still need it, and a freeze refuses the legacy row."""
    c = world.connection
    c.close()
    _alembic("downgrade", "h1f0a2c3d4e5")
    legacy_connection = _connect()
    _, volume, seg = world.volumes[2]
    legacy = str(
        _execute(
            legacy_connection,
            "INSERT INTO radiomics_features (label_series_instance_uid, features) "
            "VALUES (%s, '{\"original_firstorder_Mean\": 7}'::jsonb) RETURNING id",
            (seg,),
        )
    )
    legacy_connection.close()
    output = _alembic("upgrade", "head")
    assert "never invented" in output

    world.connection = _connect()
    c = world.connection
    row = _rows(
        c,
        "SELECT session_id, source_volume_uid, config_digest, created_by_agent_run_id "
        "FROM radiomics_features WHERE id = %s",
        (legacy,),
    )[0]
    assert row[0] is None and row[2] is None and row[3] is None
    # The source volume is derived because the SEG relation proves it.
    assert row[1] == volume
    # The NOT NULL exists and is NOT VALID for legacy rows only.
    assert _scalar(
        c,
        "SELECT convalidated FROM pg_constraint WHERE conname = 'nn_radiomics_features_session_id'",
    ) is False
    params = list(_radiomics_params(world, "A", 3, seed="after-legacy"))
    params[0] = None
    assert "not-null" in _rejects(c, _RADIOMICS_INSERT, tuple(params))

    cohort = world.cohort(world.sessions["A"]["study_population_planner_agent"], key="legacy")
    world.member(cohort, 2, 1, True)
    feature_set = str(
        _execute(
            c, _FEATURE_SET_INSERT,
            _feature_set_params(world, "A", cohort, "legacy", ["a"], {volume: [7.0]},
                                sources=[legacy]),
        )
    )
    assert "R9" in _rejects(
        c,
        "UPDATE feature_sets SET frozen_at = now() WHERE feature_set_id = %s",
        (feature_set,),
    )


# ----------------------------------------------------------------------
# Step J — model results
# ----------------------------------------------------------------------
def _artifact(world, session, kind, content=b"a,target\n1,0\n"):
    return str(
        _execute(
            world.connection,
            "INSERT INTO artifacts (session_id, kind, logical_key, display_filename, mime_type, "
            "byte_size, sha256, body, retention_class) VALUES (%s, %s, %s, 'x.csv', 'text/csv', "
            "%s, %s, %s, 'user_retained') RETURNING artifact_id",
            (
                world.sessions[session]["session_id"],
                kind,
                f"live/{uuid.uuid4().hex}",
                len(content),
                hashlib.sha256(content).hexdigest(),
                psycopg2.Binary(content),
            ),
        )
    )


_MODEL_RESULT_INSERT = (
    "INSERT INTO model_results (created_by_agent_run_id, cohort_id, feature_set_id, comparison_key, "
    "variant_key, algorithm, parameters, preprocessing, feature_order, split_definition, seeds, "
    "environment, metrics, dataset_digest, predictions_artifact_id, input_artifact_id, selected, "
    "model_file_name, model_file_digest, status) VALUES (%s, %s, %s, %s, %s, 'LogisticRegression', "
    "'{\"schema_version\": 1}'::jsonb, '{\"schema_version\": 1}'::jsonb, "
    "'{\"schema_version\": 1, \"features\": [\"a\"]}'::jsonb, '{\"schema_version\": 1}'::jsonb, "
    "'{\"schema_version\": 1}'::jsonb, '{\"schema_version\": 1}'::jsonb, %s::jsonb, %s, %s, %s, "
    "%s, %s, %s, %s) RETURNING model_result_id"
)


def _model_result_params(world, session, cohort, *, feature_set=None, comparison="cmp",
                         variant="v1", predictions=None, input_artifact=None, selected=False,
                         model_file=None, model_digest=None, status="ready",
                         agent="modeling_agent"):
    return (
        world.sessions[session][agent],
        cohort,
        feature_set,
        comparison,
        variant,
        json.dumps({"schema_version": 1, "cv": {"AUC": 0.8}}),
        _digest(f"dataset-{comparison}-{variant}"),
        predictions,
        input_artifact,
        selected,
        model_file,
        model_digest,
        status,
    )


def _frozen_feature_set(world, session, cohort, key):
    feature_set = str(
        _execute(
            world.connection, _FEATURE_SET_INSERT,
            _feature_set_params(world, session, cohort, key, ["a"], {world.volumes[0][1]: [1.0]}),
        )
    )
    _execute(
        world.connection,
        "UPDATE feature_sets SET frozen_at = now() WHERE feature_set_id = %s",
        (feature_set,),
    )
    return feature_set


def test_a_model_result_trains_on_a_frozen_same_cohort_set_or_a_captured_input(world):
    c = world.connection
    runs = world.sessions["A"]
    cohort = world.cohort(runs["study_population_planner_agent"], key="modeling")
    world.member(cohort, 0, 1, True)
    other_cohort = world.cohort(runs["study_population_planner_agent"], key="modeling-other")
    world.member(other_cohort, 0, 1, True)
    frozen = _frozen_feature_set(world, "A", cohort, "model-frozen")
    building = str(
        _execute(
            c, _FEATURE_SET_INSERT,
            _feature_set_params(
                world, "A", cohort, "model-building", ["a"], {world.volumes[0][1]: [2.0]}
            ),
        )
    )

    # The handoff is a FROZEN set (verified under a share lock).
    assert "frozen feature set" in _rejects(
        c, _MODEL_RESULT_INSERT, _model_result_params(world, "A", cohort, feature_set=building)
    )
    # A set from another cohort cannot be used.
    assert "fk_model_results_feature_set_id_cohort_id" in _rejects(
        c, _MODEL_RESULT_INSERT, _model_result_params(world, "A", other_cohort, feature_set=frozen)
    )
    # No feature set means the exact input bytes must be captured.
    assert "input_is_captured" in _rejects(
        c, _MODEL_RESULT_INSERT, _model_result_params(world, "A", cohort, variant="csv")
    )
    wrong_kind = _artifact(world, "A", "export_csv")
    assert "input_csv" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, variant="csv", input_artifact=wrong_kind),
    )
    foreign_predictions = _artifact(world, "B", "export_csv")
    assert "export_csv of session" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, feature_set=frozen, variant="p",
                             predictions=foreign_predictions),
    )
    # Feature extraction cannot write model results.
    assert "D17" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, feature_set=frozen, variant="fx",
                             agent="feature_extraction_agent"),
    )
    # A model file is identified by name AND digest.
    assert "model_file_both_or_neither" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, feature_set=frozen, variant="nodigest",
                             model_file="modeling/models/cmp/v.pkl"),
    )
    assert "model_file_name_is_relative" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, feature_set=frozen, variant="abs",
                             model_file="/srv/models/v.pkl", model_digest=_digest("pkl")),
    )
    # A cohort from another session.
    other_session_cohort = world.cohort(world.sessions["B"]["study_population_planner_agent"], key="modeling-b")
    assert "R1" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", other_session_cohort, variant="xs",
                             input_artifact=_artifact(world, "A", "input_csv")),
    )

    # A CSV-trained result with its captured input is accepted.
    _execute(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, variant="csv",
                             input_artifact=_artifact(world, "A", "input_csv")),
    )


def test_one_selected_variant_and_a_ready_result_is_final(world):
    c = world.connection
    runs = world.sessions["A"]
    cohort = world.cohort(runs["study_population_planner_agent"], key="modeling-final")
    world.member(cohort, 0, 1, True)
    frozen = _frozen_feature_set(world, "A", cohort, "final-frozen")
    predictions = _artifact(world, "A", "export_csv")

    chosen = str(
        _execute(
            c, _MODEL_RESULT_INSERT,
            _model_result_params(world, "A", cohort, feature_set=frozen, comparison="final",
                                 variant="tuned_model_1", predictions=predictions, selected=True,
                                 model_file="modeling/models/final/tuned_model_1.pkl",
                                 model_digest=_digest("tuned")),
        )
    )
    # The natural key collides on replay.
    assert "uq_model_results_created_by_agent_run_id" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, feature_set=frozen, comparison="final",
                             variant="tuned_model_1"),
    )
    # One selected variant per comparison.
    assert "uq_model_results_one_selected" in _rejects(
        c, _MODEL_RESULT_INSERT,
        _model_result_params(world, "A", cohort, feature_set=frozen, comparison="final",
                             variant="blended_model", selected=True),
    )
    # A later evaluation is a new variant, never a rewrite.
    assert "new variant" in _rejects(
        c, "UPDATE model_results SET metrics = '{\"schema_version\": 1}'::jsonb "
        "WHERE model_result_id = %s", (chosen,)
    )
    assert "cannot be deleted" in _rejects(
        c, "DELETE FROM model_results WHERE model_result_id = %s", (chosen,)
    )
    # Neither the frozen set nor the linked predictions can be deleted under it.
    assert "cannot be deleted" in _rejects(
        c, "DELETE FROM feature_sets WHERE feature_set_id = %s", (frozen,)
    )
    assert "model_results" in _rejects(c, "DELETE FROM artifacts WHERE artifact_id = %s", (predictions,))
    # A ready variant carries metrics; a selected one is ready.
    assert "ready_has_metrics" in _rejects(
        c,
        _MODEL_RESULT_INSERT.replace("%s::jsonb, %s, %s, %s, %s, %s, %s, %s)", "NULL, %s, %s, %s, %s, %s, %s, %s)"),
        tuple(
            value for index, value in enumerate(
                _model_result_params(world, "A", cohort, feature_set=frozen, comparison="final",
                                     variant="no-metrics")
            ) if index != 5
        ),
    )


def test_domain_revisions_are_reversible_on_a_populated_database(world):
    """Kept last: it downgrades through every domain revision and back."""
    world.connection.close()
    probe = _connect()
    before = {
        row[0]
        for row in _rows(probe, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    }
    probe.close()
    _alembic("downgrade", "g1f0a2c3d4e5")
    _alembic("upgrade", "head")
    probe = _connect()
    after = {
        row[0]
        for row in _rows(probe, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    }
    probe.close()
    assert before == after


def _rows(connection, sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    connection.rollback()
    return rows
