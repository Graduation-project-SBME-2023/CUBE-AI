"""current this flow: modeling — model_results"""

from typing import Sequence, Union

from alembic import op


revision: str = "j1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "i1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors application_api/core/json_bounds.py; the contract test checks it.
DOMAIN_JSON_MAX_BYTES = 1024 * 1024


def _versioned(column: str, *, nullable: bool) -> str:
    rule = (
        f"(jsonb_typeof({column}) = 'object' "
        f"AND jsonb_typeof({column}->'schema_version') = 'number' "
        f"AND pg_column_size({column}) <= {DOMAIN_JSON_MAX_BYTES})"
    )
    return f"({column} IS NULL OR {rule})" if nullable else rule


_ROW_RULES = """
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

    -- The handoff contract (§7 J): a FROZEN set, verified under a lock that
    -- keeps it from changing until this transaction ends.
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
    op.execute(
        f"""
        CREATE TABLE model_results (
            model_result_id uuid NOT NULL DEFAULT gen_random_uuid(),
            created_by_agent_run_id uuid NOT NULL,
            cohort_id uuid NOT NULL,
            feature_set_id uuid,
            comparison_key text NOT NULL,
            variant_key text NOT NULL,
            algorithm text NOT NULL,
            parameters jsonb NOT NULL,
            preprocessing jsonb NOT NULL,
            feature_order jsonb NOT NULL,
            split_definition jsonb NOT NULL,
            seeds jsonb NOT NULL,
            environment jsonb NOT NULL,
            metrics jsonb,
            dataset_digest text NOT NULL,
            predictions_artifact_id uuid,
            input_artifact_id uuid,
            selected boolean NOT NULL DEFAULT false,
            model_file_name text,
            model_file_digest text,
            status text NOT NULL DEFAULT 'building',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_model_results PRIMARY KEY (model_result_id),
            -- The natural key a replay collides on (R15).
            CONSTRAINT uq_model_results_created_by_agent_run_id
                UNIQUE (created_by_agent_run_id, comparison_key, variant_key),
            CONSTRAINT fk_model_results_created_by_agent_run_id_agent_runs
                FOREIGN KEY (created_by_agent_run_id)
                REFERENCES agent_runs (agent_run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_model_results_cohort_id_cohorts
                FOREIGN KEY (cohort_id) REFERENCES cohorts (cohort_id) ON DELETE RESTRICT,
            CONSTRAINT fk_model_results_feature_set_id_feature_sets
                FOREIGN KEY (feature_set_id)
                REFERENCES feature_sets (feature_set_id) ON DELETE RESTRICT,
            -- R1: a set from another cohort cannot be used.
            CONSTRAINT fk_model_results_feature_set_id_cohort_id
                FOREIGN KEY (feature_set_id, cohort_id)
                REFERENCES feature_sets (feature_set_id, cohort_id) ON DELETE RESTRICT,
            CONSTRAINT fk_model_results_predictions_artifact_id_artifacts
                FOREIGN KEY (predictions_artifact_id)
                REFERENCES artifacts (artifact_id) ON DELETE RESTRICT,
            CONSTRAINT fk_model_results_input_artifact_id_artifacts
                FOREIGN KEY (input_artifact_id)
                REFERENCES artifacts (artifact_id) ON DELETE RESTRICT,
            CONSTRAINT ck_model_results_comparison_key_not_blank
                CHECK (length(btrim(comparison_key)) > 0),
            CONSTRAINT ck_model_results_variant_key_not_blank
                CHECK (length(btrim(variant_key)) > 0),
            CONSTRAINT ck_model_results_algorithm_not_blank
                CHECK (length(btrim(algorithm)) > 0),
            CONSTRAINT ck_model_results_status CHECK (status IN ('building', 'ready', 'failed')),
            CONSTRAINT ck_model_results_dataset_digest_is_sha256
                CHECK (dataset_digest ~ '^[0-9a-f]{{64}}$'),
            -- R6: no fake feature set — a CSV-trained result captures its input.
            CONSTRAINT ck_model_results_input_is_captured
                CHECK (feature_set_id IS NOT NULL OR input_artifact_id IS NOT NULL),
            -- A1: the binary is identified by name AND digest, or not at all.
            CONSTRAINT ck_model_results_model_file_both_or_neither
                CHECK ((model_file_name IS NULL) = (model_file_digest IS NULL)),
            CONSTRAINT ck_model_results_model_file_digest_is_sha256
                CHECK (model_file_digest IS NULL OR model_file_digest ~ '^[0-9a-f]{{64}}$'),
            -- A relative name under the modeling root, never a host path.
            CONSTRAINT ck_model_results_model_file_name_is_relative CHECK (
                model_file_name IS NULL OR (
                    model_file_name !~ '^([a-zA-Z]:)?[\\\\/]'
                    AND model_file_name !~ '(^|[\\\\/])\\.\\.([\\\\/]|$)'
                )
            ),
            CONSTRAINT ck_model_results_selected_is_ready
                CHECK (NOT selected OR status = 'ready'),
            CONSTRAINT ck_model_results_ready_has_metrics
                CHECK (status <> 'ready' OR metrics IS NOT NULL),
            CONSTRAINT ck_model_results_parameters_versioned
                CHECK ({_versioned('parameters', nullable=False)}),
            CONSTRAINT ck_model_results_preprocessing_versioned
                CHECK ({_versioned('preprocessing', nullable=False)}),
            CONSTRAINT ck_model_results_feature_order_versioned
                CHECK ({_versioned('feature_order', nullable=False)}),
            CONSTRAINT ck_model_results_split_definition_versioned
                CHECK ({_versioned('split_definition', nullable=False)}),
            CONSTRAINT ck_model_results_seeds_versioned
                CHECK ({_versioned('seeds', nullable=False)}),
            CONSTRAINT ck_model_results_environment_versioned
                CHECK ({_versioned('environment', nullable=False)}),
            CONSTRAINT ck_model_results_metrics_versioned
                CHECK ({_versioned('metrics', nullable=True)})
        )
        """
    )
    # One selected variant per comparison of a run.
    op.execute(
        "CREATE UNIQUE INDEX uq_model_results_one_selected "
        "ON model_results (created_by_agent_run_id, comparison_key) WHERE selected"
    )
    for name, column in (
        ("ix_model_results_cohort_id", "cohort_id"),
        ("ix_model_results_feature_set_id", "feature_set_id"),
        ("ix_model_results_predictions_artifact_id", "predictions_artifact_id"),
        ("ix_model_results_input_artifact_id", "input_artifact_id"),
    ):
        op.execute(f"CREATE INDEX {name} ON model_results ({column})")

    op.execute(
        "CREATE TRIGGER model_results_cohort_alignment BEFORE INSERT ON model_results "
        "FOR EACH ROW EXECUTE FUNCTION domain_rows_enforce_cohort_alignment()"
    )
    op.execute(
        "CREATE TRIGGER model_results_producer BEFORE INSERT ON model_results "
        "FOR EACH ROW EXECUTE FUNCTION domain_rows_enforce_producer('modeling_agent')"
    )
    op.execute(_ROW_RULES)
    op.execute(
        "CREATE TRIGGER model_results_row_rules BEFORE INSERT OR UPDATE OR DELETE ON model_results "
        "FOR EACH ROW EXECUTE FUNCTION model_results_enforce_row_rules()"
    )


def downgrade() -> None:
    for trigger in (
        "model_results_row_rules",
        "model_results_producer",
        "model_results_cohort_alignment",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON model_results")
    op.execute("DROP TABLE IF EXISTS model_results")
    op.execute("DROP FUNCTION IF EXISTS model_results_enforce_row_rules()")
