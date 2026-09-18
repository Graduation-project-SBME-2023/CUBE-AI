"""fix model_results_enforce_row_rules(): read feature_sets.frozen_at, not the status column v1_l dropped"""

from typing import Sequence, Union

from alembic import op


revision: str = "n1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "m1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same body as j1f0a2c3d4e5's _ROW_RULES, but the feature_sets.status lookup
# (dropped by l1f0a2c3d4e5, plan item 15) is replaced by frozen_at, which
# status was always kept in lockstep with: status = 'frozen' <=> frozen_at
# IS NOT NULL. Every model_results INSERT with a feature_set_id fires this
# trigger, so the stale column reference broke every modeling training call.
_ROW_RULES = """
CREATE OR REPLACE FUNCTION model_results_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    set_frozen_at timestamptz;
    run_session uuid;
    artifact_session uuid;
    artifact_kind text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'model result % is research history and cannot be deleted', OLD.model_result_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.model_result_id IS DISTINCT FROM OLD.model_result_id
           OR NEW.created_by_agent_run_id IS DISTINCT FROM OLD.created_by_agent_run_id
           OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
           OR NEW.feature_set_id IS DISTINCT FROM OLD.feature_set_id
           OR NEW.comparison_key IS DISTINCT FROM OLD.comparison_key
           OR NEW.variant_key IS DISTINCT FROM OLD.variant_key
           OR NEW.algorithm IS DISTINCT FROM OLD.algorithm
           OR NEW.feature_order IS DISTINCT FROM OLD.feature_order
           OR NEW.split_definition IS DISTINCT FROM OLD.split_definition
           OR NEW.seeds IS DISTINCT FROM OLD.seeds
           OR NEW.dataset_digest IS DISTINCT FROM OLD.dataset_digest
           OR NEW.input_artifact_id IS DISTINCT FROM OLD.input_artifact_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION
                'model result % identity, data and split are immutable', OLD.model_result_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF OLD.status IN ('ready', 'failed') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION
                'model result % is %; a later evaluation is recorded as a new variant, '
                'never a rewrite', OLD.model_result_id, OLD.status
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    -- The handoff contract (§7 J): a FROZEN set, verified under a lock that
    -- keeps it from changing until this transaction ends.
    IF NEW.feature_set_id IS NOT NULL THEN
        SELECT frozen_at INTO set_frozen_at
        FROM feature_sets WHERE feature_set_id = NEW.feature_set_id
        FOR SHARE;
        IF FOUND AND set_frozen_at IS NULL THEN
            RAISE EXCEPTION
                'model result must train on a frozen feature set; % is not frozen',
                NEW.feature_set_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    SELECT session_id INTO run_session
    FROM agent_runs WHERE agent_run_id = NEW.created_by_agent_run_id;

    IF NEW.predictions_artifact_id IS NOT NULL THEN
        SELECT session_id, kind INTO artifact_session, artifact_kind
        FROM artifacts WHERE artifact_id = NEW.predictions_artifact_id;
        IF FOUND AND (artifact_session IS DISTINCT FROM run_session
                      OR artifact_kind <> 'export_csv') THEN
            RAISE EXCEPTION
                'predictions artifact % must be an export_csv of session % (R1/R6)',
                NEW.predictions_artifact_id, run_session
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    IF NEW.input_artifact_id IS NOT NULL THEN
        SELECT session_id, kind INTO artifact_session, artifact_kind
        FROM artifacts WHERE artifact_id = NEW.input_artifact_id;
        IF FOUND AND (artifact_session IS DISTINCT FROM run_session
                      OR artifact_kind <> 'input_csv') THEN
            RAISE EXCEPTION
                'input artifact % must be an input_csv of session % (R1/R6)',
                NEW.input_artifact_id, run_session
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

# What the function looked like before this fix (feature_sets.status lookup),
# for downgrade().
_ROW_RULES_PRE_FIX = """
CREATE OR REPLACE FUNCTION model_results_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    set_status text;
    run_session uuid;
    artifact_session uuid;
    artifact_kind text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'model result % is research history and cannot be deleted', OLD.model_result_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.model_result_id IS DISTINCT FROM OLD.model_result_id
           OR NEW.created_by_agent_run_id IS DISTINCT FROM OLD.created_by_agent_run_id
           OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
           OR NEW.feature_set_id IS DISTINCT FROM OLD.feature_set_id
           OR NEW.comparison_key IS DISTINCT FROM OLD.comparison_key
           OR NEW.variant_key IS DISTINCT FROM OLD.variant_key
           OR NEW.algorithm IS DISTINCT FROM OLD.algorithm
           OR NEW.feature_order IS DISTINCT FROM OLD.feature_order
           OR NEW.split_definition IS DISTINCT FROM OLD.split_definition
           OR NEW.seeds IS DISTINCT FROM OLD.seeds
           OR NEW.dataset_digest IS DISTINCT FROM OLD.dataset_digest
           OR NEW.input_artifact_id IS DISTINCT FROM OLD.input_artifact_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION
                'model result % identity, data and split are immutable', OLD.model_result_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF OLD.status IN ('ready', 'failed') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION
                'model result % is %; a later evaluation is recorded as a new variant, '
                'never a rewrite', OLD.model_result_id, OLD.status
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    IF NEW.feature_set_id IS NOT NULL THEN
        SELECT status INTO set_status
        FROM feature_sets WHERE feature_set_id = NEW.feature_set_id
        FOR SHARE;
        IF FOUND AND set_status <> 'frozen' THEN
            RAISE EXCEPTION
                'model result must train on a frozen feature set; % is %',
                NEW.feature_set_id, set_status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    SELECT session_id INTO run_session
    FROM agent_runs WHERE agent_run_id = NEW.created_by_agent_run_id;

    IF NEW.predictions_artifact_id IS NOT NULL THEN
        SELECT session_id, kind INTO artifact_session, artifact_kind
        FROM artifacts WHERE artifact_id = NEW.predictions_artifact_id;
        IF FOUND AND (artifact_session IS DISTINCT FROM run_session
                      OR artifact_kind <> 'export_csv') THEN
            RAISE EXCEPTION
                'predictions artifact % must be an export_csv of session % (R1/R6)',
                NEW.predictions_artifact_id, run_session
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    IF NEW.input_artifact_id IS NOT NULL THEN
        SELECT session_id, kind INTO artifact_session, artifact_kind
        FROM artifacts WHERE artifact_id = NEW.input_artifact_id;
        IF FOUND AND (artifact_session IS DISTINCT FROM run_session
                      OR artifact_kind <> 'input_csv') THEN
            RAISE EXCEPTION
                'input artifact % must be an input_csv of session % (R1/R6)',
                NEW.input_artifact_id, run_session
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_ROW_RULES)


def downgrade() -> None:
    op.execute(_ROW_RULES_PRE_FIX)
