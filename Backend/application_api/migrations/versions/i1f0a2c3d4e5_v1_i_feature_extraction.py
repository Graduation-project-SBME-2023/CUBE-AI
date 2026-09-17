"""current this flow: feature extraction — radiomics provenance and frozen feature sets"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "h1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors application_api/core/json_bounds.py; the contract test checks it.
DOMAIN_JSON_MAX_BYTES = 1024 * 1024
FEATURE_MATRIX_MAX_BYTES = 16 * 1024 * 1024

# Required for every row written from this revision on.
PROVENANCE_COLUMNS = (
    "session_id",
    "label_series_instance_uid",
    "source_volume_uid",
    "segment_number",
    "extractor_name",
    "extractor_version",
    "configuration",
    "config_digest",
    "image_digest",
    "segmentation_digest",
    "source_digest",
    "schema_version",
)
DIGESTS = ("config_digest", "image_digest", "segmentation_digest", "source_digest")


def _versioned(column: str, *, nullable: bool) -> str:
    rule = (
        f"(jsonb_typeof({column}) = 'object' "
        f"AND jsonb_typeof({column}->'schema_version') = 'number' "
        f"AND pg_column_size({column}) <= {DOMAIN_JSON_MAX_BYTES})"
    )
    return f"({column} IS NULL OR {rule})" if nullable else rule


def _digest_rule(columns: Sequence[str]) -> str:
    return " AND ".join(f"{column} ~ '^[0-9a-f]{{64}}$'" for column in columns)


_PRODUCER_RULES = """
CREATE OR REPLACE FUNCTION domain_rows_enforce_producer()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    producer text;
BEGIN
    IF NEW.created_by_agent_run_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT s.agent_type INTO producer
    FROM agent_runs r JOIN plan_steps s ON s.step_id = r.step_id
    WHERE r.agent_run_id = NEW.created_by_agent_run_id;
    IF producer IS NULL THEN
        -- The FK reports the missing run with the right error.
        RETURN NEW;
    END IF;
    IF NOT (producer = ANY (TG_ARGV)) THEN
        RAISE EXCEPTION
            '% rows are owned by %; agent run % executes % (D17)',
            TG_TABLE_NAME, array_to_string(TG_ARGV, ' or '),
            NEW.created_by_agent_run_id, producer
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""

_RADIOMICS_ROW_RULES = """
CREATE OR REPLACE FUNCTION radiomics_features_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pair record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'radiomics row % is research history and cannot be deleted', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'radiomics row % is immutable once written (A7); a different measurement '
            'is a different content-addressed row', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.segmentation_pair_id IS NOT NULL THEN
        SELECT p.volume_series_instance_uid AS volume,
               p.selected_segmentation_uid AS selected,
               p.segment_number AS segment_number,
               p.review_decision AS decision,
               r.session_id AS session_id
          INTO pair
          FROM segmentation_pairs p
          JOIN segmentation_runs sr ON sr.segmentation_run_id = p.segmentation_run_id
          JOIN agent_runs r ON r.agent_run_id = sr.created_by_agent_run_id
         WHERE p.pair_id = NEW.segmentation_pair_id;
        IF FOUND THEN
            IF pair.session_id IS DISTINCT FROM NEW.session_id THEN
                RAISE EXCEPTION
                    'radiomics row of session % cannot cite segmentation pair % of '
                    'session % (R1)', NEW.session_id, NEW.segmentation_pair_id, pair.session_id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF pair.volume IS DISTINCT FROM NEW.source_volume_uid
               OR pair.selected IS DISTINCT FROM NEW.label_series_instance_uid
               OR pair.segment_number IS DISTINCT FROM NEW.segment_number THEN
                RAISE EXCEPTION
                    'radiomics row must measure the selected SEG %, segment %, of volume % '
                    'that pair % names', pair.selected, pair.segment_number, pair.volume,
                    NEW.segmentation_pair_id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF pair.decision IS NULL OR pair.decision NOT IN ('accepted', 'modified') THEN
                RAISE EXCEPTION
                    'segmentation pair % is not an accepted or modified mask; a pipeline '
                    'mask is not usable until its review decision is set',
                    NEW.segmentation_pair_id
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

_RECTANGULAR = """
CREATE OR REPLACE FUNCTION feature_matrix_is_rectangular(names jsonb, matrix jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN jsonb_typeof(names) IS DISTINCT FROM 'array'
          OR jsonb_typeof(matrix) IS DISTINCT FROM 'object' THEN false
        ELSE (
            SELECT coalesce(bool_and(
                CASE WHEN jsonb_typeof(v) = 'array'
                     THEN jsonb_array_length(v) = jsonb_array_length(names)
                     ELSE false END), true)
            FROM jsonb_each(matrix) AS e(k, v)
        )
    END
$$;
"""

_FEATURE_SET_ROW_RULES = """
CREATE OR REPLACE FUNCTION feature_sets_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    offending integer;
    nulls integer;
    subjects integer;
    names integer;
    distinct_names integer;
    label_field text;
    set_session uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'feature set % is modeling input provenance and cannot be deleted',
            OLD.feature_set_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.feature_set_id IS DISTINCT FROM OLD.feature_set_id
       OR NEW.created_by_agent_run_id IS DISTINCT FROM OLD.created_by_agent_run_id
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.set_key IS DISTINCT FROM OLD.set_key
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'feature set % identity is immutable', OLD.feature_set_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'frozen' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'feature set % is frozen and immutable', OLD.feature_set_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status = 'frozen' AND OLD.status = 'building' THEN
        -- Every matrix key is a labelled volume of this set's cohort. The
        -- label is per volume (cohort_members.label), not per subject: one
        -- patient may legitimately contribute more than one matrix row.
        SELECT count(*) INTO offending
        FROM jsonb_object_keys(NEW.feature_matrix) AS k(volume)
        WHERE NOT EXISTS (
            SELECT 1 FROM cohort_members m
            WHERE m.cohort_id = NEW.cohort_id AND m.volume_series_instance_uid = k.volume);
        IF offending > 0 THEN
            RAISE EXCEPTION
                'feature set %: % matrix key(s) are not volume UIDs of cohort %',
                NEW.feature_set_id, offending, NEW.cohort_id
                USING ERRCODE = 'check_violation';
        END IF;

        SELECT count(*) INTO subjects FROM jsonb_object_keys(NEW.feature_matrix);
        IF subjects = 0 THEN
            RAISE EXCEPTION 'feature set % has an empty matrix', NEW.feature_set_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF jsonb_typeof(NEW.quality_summary->'subjects') = 'number'
           AND (NEW.quality_summary->>'subjects')::numeric <> subjects THEN
            RAISE EXCEPTION
                'feature set %: quality_summary.subjects disagrees with the matrix (% rows)',
                NEW.feature_set_id, subjects
                USING ERRCODE = 'check_violation';
        END IF;

        -- Names: distinct strings, never an identity or label column.
        SELECT label_definition->>'field' INTO label_field
        FROM cohorts WHERE cohort_id = NEW.cohort_id;
        SELECT count(*), count(DISTINCT value) INTO names, distinct_names
        FROM jsonb_array_elements(NEW.feature_names) AS e(value);
        SELECT count(*) INTO offending
        FROM jsonb_array_elements(NEW.feature_names) AS e(value)
        WHERE jsonb_typeof(value) <> 'string'
           OR lower(btrim(value #>> '{}')) IN
              ('target', 'label', 'subject_key', 'volume_series_instance_uid', 'ordinal')
           OR (value #>> '{}') = label_field;
        IF offending > 0 OR names <> distinct_names THEN
            RAISE EXCEPTION
                'feature set %: feature names must be distinct strings and never the '
                'label or an identity column', NEW.feature_set_id
                USING ERRCODE = 'check_violation';
        END IF;

        -- Cells are numbers, or missing values with a recorded reason (§2.1).
        SELECT count(*) FILTER (WHERE jsonb_typeof(cell) NOT IN ('number', 'null')),
               count(*) FILTER (WHERE jsonb_typeof(cell) = 'null')
          INTO offending, nulls
          FROM jsonb_each(NEW.feature_matrix) AS r(subject, cells),
               jsonb_array_elements(r.cells) AS c(cell);
        IF offending > 0 THEN
            RAISE EXCEPTION
                'feature set %: % matrix cell(s) are not numbers', NEW.feature_set_id, offending
                USING ERRCODE = 'check_violation';
        END IF;
        IF nulls > 0
           AND coalesce(btrim(NEW.quality_summary #>> '{missing_values,reason}'), '') = '' THEN
            RAISE EXCEPTION
                'feature set %: % missing value(s) without quality_summary.missing_values.reason',
                NEW.feature_set_id, nulls
                USING ERRCODE = 'check_violation';
        END IF;

        -- Sources: no duplicate, same session, never a legacy row (R9).
        IF NEW.source_feature_ids IS NOT NULL THEN
            SELECT r.session_id INTO set_session
            FROM agent_runs r WHERE r.agent_run_id = NEW.created_by_agent_run_id;
            SELECT count(*) - count(DISTINCT value) INTO offending
            FROM jsonb_array_elements_text(NEW.source_feature_ids) AS s(value);
            IF offending > 0 THEN
                RAISE EXCEPTION
                    'feature set % repeats % source radiomics row(s)', NEW.feature_set_id, offending
                    USING ERRCODE = 'check_violation';
            END IF;
            SELECT count(*) INTO offending
            FROM jsonb_array_elements_text(NEW.source_feature_ids) AS s(value)
            LEFT JOIN radiomics_features f ON f.id::text = s.value
            WHERE f.id IS NULL
               OR f.session_id IS DISTINCT FROM set_session
               OR f.source_digest IS NULL;
            IF offending > 0 THEN
                RAISE EXCEPTION
                    'feature set %: % source row(s) are missing, from another session, or '
                    'legacy rows with NULL provenance (R9)', NEW.feature_set_id, offending
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""


def _count(bind, sql: str) -> int:
    return int(bind.execute(sa.text(sql)).scalar_one())


def _has_column(bind, table: str, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _add_check(bind, name: str, rule: str, violations_sql: str) -> None:
    """A CHECK that legacy rows might violate is NOT VALID for them only."""
    violations = _count(bind, violations_sql)
    validity = " NOT VALID" if violations else ""
    op.execute(f"ALTER TABLE radiomics_features ADD CONSTRAINT {name} CHECK ({rule}){validity}")
    if violations:
        print(
            f"[v1 step I] {violations} legacy radiomics row(s) violate {name}; the CHECK is "
            "NOT VALID for them and enforced for every new row."
        )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. radiomics_features — provenance columns
    # ------------------------------------------------------------------
    if _has_column(bind, "radiomics_features", "updated_at"):
        # Immutable once consumed; no generic update timestamp.
        op.execute("ALTER TABLE radiomics_features DROP COLUMN updated_at")
    op.execute(
        """
        ALTER TABLE radiomics_features
            ADD COLUMN session_id uuid,
            ADD COLUMN source_volume_uid text,
            ADD COLUMN segment_number integer,
            ADD COLUMN segmentation_pair_id uuid,
            ADD COLUMN created_by_agent_run_id uuid,
            ADD COLUMN extractor_name text,
            ADD COLUMN extractor_version text,
            ADD COLUMN configuration jsonb,
            ADD COLUMN config_digest text,
            ADD COLUMN image_digest text,
            ADD COLUMN segmentation_digest text,
            ADD COLUMN source_digest text,
            ADD COLUMN schema_version integer
        """
    )

    legacy = _count(bind, "SELECT count(*) FROM radiomics_features")
    # The source volume is derived only where the SEG relation proves it.
    op.execute(
        """
        UPDATE radiomics_features r
        SET source_volume_uid = s.volume_series_instance_uid
        FROM segmentations s
        WHERE s.label_series_instance_uid = r.label_series_instance_uid
          AND r.source_volume_uid IS NULL
        """
    )
    derived = _count(
        bind, "SELECT count(*) FROM radiomics_features WHERE source_volume_uid IS NOT NULL"
    )
    print(
        f"[v1 step I] {legacy} legacy radiomics row(s); source_volume_uid derived from the SEG "
        f"relation for {derived}. Session, extractor, configuration and digests stay NULL "
        "for them — unknown, and never invented (R9)."
    )

    # NOT NULL: valid where no legacy row is NULL, NOT VALID otherwise.
    for column in PROVENANCE_COLUMNS:
        nulls = _count(bind, f"SELECT count(*) FROM radiomics_features WHERE {column} IS NULL")
        if nulls == 0:
            op.execute(f"ALTER TABLE radiomics_features ALTER COLUMN {column} SET NOT NULL")
        else:
            op.execute(
                f"ALTER TABLE radiomics_features ADD CONSTRAINT "
                f"nn_radiomics_features_{column} NOT NULL {column} NOT VALID"
            )

    _add_check(
        bind,
        "ck_radiomics_features_segment_number_positive",
        "segment_number > 0",
        "SELECT count(*) FROM radiomics_features WHERE segment_number <= 0",
    )
    _add_check(
        bind,
        "ck_radiomics_features_digests_are_sha256",
        _digest_rule(DIGESTS),
        f"SELECT count(*) FROM radiomics_features WHERE NOT ({_digest_rule(DIGESTS)})",
    )
    _add_check(
        bind,
        "ck_radiomics_features_extractor_not_blank",
        "length(btrim(extractor_name)) > 0 AND length(btrim(extractor_version)) > 0",
        "SELECT count(*) FROM radiomics_features "
        "WHERE NOT (length(btrim(extractor_name)) > 0 AND length(btrim(extractor_version)) > 0)",
    )
    features_rule = (
        "jsonb_typeof(features) = 'object' "
        f"AND pg_column_size(features) <= {DOMAIN_JSON_MAX_BYTES}"
    )
    _add_check(
        bind,
        "ck_radiomics_features_features_bounded",
        features_rule,
        f"SELECT count(*) FROM radiomics_features WHERE NOT ({features_rule})",
    )
    configuration_rule = (
        "configuration IS NULL OR (jsonb_typeof(configuration) = 'object' "
        f"AND pg_column_size(configuration) <= {DOMAIN_JSON_MAX_BYTES})"
    )
    _add_check(
        bind,
        "ck_radiomics_features_configuration_bounded",
        configuration_rule,
        f"SELECT count(*) FROM radiomics_features WHERE NOT ({configuration_rule})",
    )
    _add_check(
        bind,
        "ck_radiomics_features_schema_version_positive",
        "schema_version >= 1",
        "SELECT count(*) FROM radiomics_features WHERE schema_version < 1",
    )

    op.execute(
        """
        ALTER TABLE radiomics_features
            ADD CONSTRAINT fk_radiomics_features_session_id_sessions
                FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_radiomics_features_created_by_agent_run_id_agent_runs
                FOREIGN KEY (created_by_agent_run_id)
                REFERENCES agent_runs (agent_run_id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_radiomics_features_created_by_agent_run_id_session_id
                FOREIGN KEY (created_by_agent_run_id, session_id)
                REFERENCES agent_runs (agent_run_id, session_id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_radiomics_features_segmentation_pair_id_segmentation_pairs
                FOREIGN KEY (segmentation_pair_id)
                REFERENCES segmentation_pairs (pair_id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_radiomics_features_segmentation
                FOREIGN KEY (label_series_instance_uid, source_volume_uid)
                REFERENCES segmentations (label_series_instance_uid, volume_series_instance_uid)
                ON DELETE RESTRICT,
            ADD CONSTRAINT uq_radiomics_features_session_id UNIQUE (
                session_id, label_series_instance_uid, segment_number,
                config_digest, source_digest
            )
        """
    )
    op.execute(
        "CREATE INDEX ix_radiomics_features_created_by_agent_run_id "
        "ON radiomics_features (created_by_agent_run_id)"
    )
    op.execute(
        "CREATE INDEX ix_radiomics_features_segmentation_pair_id "
        "ON radiomics_features (segmentation_pair_id)"
    )

    op.execute(_PRODUCER_RULES)
    op.execute(_RADIOMICS_ROW_RULES)
    op.execute(
        "CREATE TRIGGER radiomics_features_producer BEFORE INSERT ON radiomics_features "
        "FOR EACH ROW EXECUTE FUNCTION domain_rows_enforce_producer('feature_extraction_agent')"
    )
    op.execute(
        "CREATE TRIGGER radiomics_features_row_rules "
        "BEFORE INSERT OR UPDATE OR DELETE ON radiomics_features "
        "FOR EACH ROW EXECUTE FUNCTION radiomics_features_enforce_row_rules()"
    )

    # ------------------------------------------------------------------
    # 2. feature_sets
    # ------------------------------------------------------------------
    op.execute(_RECTANGULAR)
    op.execute(
        f"""
        CREATE TABLE feature_sets (
            feature_set_id uuid NOT NULL DEFAULT gen_random_uuid(),
            created_by_agent_run_id uuid NOT NULL,
            cohort_id uuid NOT NULL,
            set_key text NOT NULL,
            schema_version integer NOT NULL DEFAULT 1,
            feature_names jsonb NOT NULL,
            feature_matrix jsonb NOT NULL,
            source_feature_ids jsonb,
            selection jsonb NOT NULL,
            extraction_config jsonb,
            analysis jsonb,
            quality_summary jsonb,
            config_digest text NOT NULL,
            source_digest text NOT NULL,
            manifest_digest text NOT NULL,
            status text NOT NULL DEFAULT 'building',
            frozen_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_feature_sets PRIMARY KEY (feature_set_id),
            CONSTRAINT uq_feature_sets_created_by_agent_run_id
                UNIQUE (created_by_agent_run_id, set_key),
            -- R1: the tuple model_results' composite FK targets (step J).
            CONSTRAINT uq_feature_sets_feature_set_id UNIQUE (feature_set_id, cohort_id),
            CONSTRAINT fk_feature_sets_created_by_agent_run_id_agent_runs
                FOREIGN KEY (created_by_agent_run_id)
                REFERENCES agent_runs (agent_run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_feature_sets_cohort_id_cohorts
                FOREIGN KEY (cohort_id) REFERENCES cohorts (cohort_id) ON DELETE RESTRICT,
            CONSTRAINT ck_feature_sets_set_key_not_blank CHECK (length(btrim(set_key)) > 0),
            CONSTRAINT ck_feature_sets_schema_version_positive CHECK (schema_version >= 1),
            CONSTRAINT ck_feature_sets_feature_names_is_array CHECK (
                CASE WHEN jsonb_typeof(feature_names) = 'array'
                     THEN jsonb_array_length(feature_names) > 0 ELSE false END
            ),
            CONSTRAINT ck_feature_sets_feature_matrix_bounded CHECK (
                jsonb_typeof(feature_matrix) = 'object'
                AND pg_column_size(feature_matrix) <= {FEATURE_MATRIX_MAX_BYTES}
            ),
            -- Rule 1 of §2.6 item 22: rectangularity is a constraint.
            CONSTRAINT ck_feature_sets_feature_matrix_shape
                CHECK (feature_matrix_is_rectangular(feature_names, feature_matrix)),
            CONSTRAINT ck_feature_sets_source_feature_ids_is_array CHECK (
                source_feature_ids IS NULL OR jsonb_typeof(source_feature_ids) = 'array'
            ),
            CONSTRAINT ck_feature_sets_selection_versioned
                CHECK ({_versioned('selection', nullable=False)}),
            CONSTRAINT ck_feature_sets_selection_states_label_supervision CHECK (
                jsonb_typeof(selection->'label_supervised_on_full_data') = 'boolean'
            ),
            CONSTRAINT ck_feature_sets_extraction_config_versioned
                CHECK ({_versioned('extraction_config', nullable=True)}),
            CONSTRAINT ck_feature_sets_analysis_versioned
                CHECK ({_versioned('analysis', nullable=True)}),
            CONSTRAINT ck_feature_sets_quality_summary_versioned
                CHECK ({_versioned('quality_summary', nullable=True)}),
            CONSTRAINT ck_feature_sets_digests_are_sha256 CHECK (
                {_digest_rule(("config_digest", "source_digest", "manifest_digest"))}
            ),
            CONSTRAINT ck_feature_sets_status CHECK (status IN ('building', 'frozen')),
            CONSTRAINT ck_feature_sets_frozen_at_iff_frozen
                CHECK ((status = 'frozen') = (frozen_at IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX ix_feature_sets_cohort_id ON feature_sets (cohort_id)")
    # H's shared function: the cohort is from the set's own session, and the
    # first downstream use freezes it.
    op.execute(
        "CREATE TRIGGER feature_sets_cohort_alignment BEFORE INSERT ON feature_sets "
        "FOR EACH ROW EXECUTE FUNCTION domain_rows_enforce_cohort_alignment()"
    )
    op.execute(
        "CREATE TRIGGER feature_sets_producer BEFORE INSERT ON feature_sets "
        "FOR EACH ROW EXECUTE FUNCTION domain_rows_enforce_producer('feature_extraction_agent')"
    )
    op.execute(_FEATURE_SET_ROW_RULES)
    op.execute(
        "CREATE TRIGGER feature_sets_row_rules BEFORE UPDATE OR DELETE ON feature_sets "
        "FOR EACH ROW EXECUTE FUNCTION feature_sets_enforce_row_rules()"
    )


def downgrade() -> None:
    for trigger, table in (
        ("feature_sets_row_rules", "feature_sets"),
        ("feature_sets_producer", "feature_sets"),
        ("feature_sets_cohort_alignment", "feature_sets"),
        ("radiomics_features_row_rules", "radiomics_features"),
        ("radiomics_features_producer", "radiomics_features"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("DROP TABLE IF EXISTS feature_sets")
    for function in (
        "feature_sets_enforce_row_rules()",
        "feature_matrix_is_rectangular(jsonb, jsonb)",
        "radiomics_features_enforce_row_rules()",
        "domain_rows_enforce_producer()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")

    op.execute("DROP INDEX IF EXISTS ix_radiomics_features_segmentation_pair_id")
    op.execute("DROP INDEX IF EXISTS ix_radiomics_features_created_by_agent_run_id")
    for constraint in (
        "uq_radiomics_features_session_id",
        "fk_radiomics_features_segmentation",
        "fk_radiomics_features_segmentation_pair_id_segmentation_pairs",
        "fk_radiomics_features_created_by_agent_run_id_session_id",
        "fk_radiomics_features_created_by_agent_run_id_agent_runs",
        "fk_radiomics_features_session_id_sessions",
        "ck_radiomics_features_schema_version_positive",
        "ck_radiomics_features_configuration_bounded",
        "ck_radiomics_features_features_bounded",
        "ck_radiomics_features_extractor_not_blank",
        "ck_radiomics_features_digests_are_sha256",
        "ck_radiomics_features_segment_number_positive",
        *(f"nn_radiomics_features_{column}" for column in PROVENANCE_COLUMNS),
    ):
        op.execute(f"ALTER TABLE radiomics_features DROP CONSTRAINT IF EXISTS {constraint}")
    # Pre-I the SEG reference was nullable in the ORM; restore that.
    op.execute(
        "ALTER TABLE radiomics_features ALTER COLUMN label_series_instance_uid DROP NOT NULL"
    )
    op.execute(
        """
        ALTER TABLE radiomics_features
            DROP COLUMN IF EXISTS schema_version,
            DROP COLUMN IF EXISTS source_digest,
            DROP COLUMN IF EXISTS segmentation_digest,
            DROP COLUMN IF EXISTS image_digest,
            DROP COLUMN IF EXISTS config_digest,
            DROP COLUMN IF EXISTS configuration,
            DROP COLUMN IF EXISTS extractor_version,
            DROP COLUMN IF EXISTS extractor_name,
            DROP COLUMN IF EXISTS created_by_agent_run_id,
            DROP COLUMN IF EXISTS segmentation_pair_id,
            DROP COLUMN IF EXISTS segment_number,
            DROP COLUMN IF EXISTS source_volume_uid,
            DROP COLUMN IF EXISTS session_id
        """
    )
