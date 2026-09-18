"""fix feature_sets_enforce_row_rules(): status column was dropped by v1_l, trigger wasn't updated"""

from typing import Sequence, Union

from alembic import op


revision: str = "m1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "l1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same body as i1f0a2c3d4e5's _FEATURE_SET_ROW_RULES, but OLD.status / NEW.status
# (dropped by l1f0a2c3d4e5, plan item 15) are replaced by the frozen_at they were
# always kept in lockstep with: status = 'frozen' <=> frozen_at IS NOT NULL.
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

    IF OLD.frozen_at IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'feature set % is frozen and immutable', OLD.feature_set_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.frozen_at IS NOT NULL AND OLD.frozen_at IS NULL THEN
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

# What the function looked like before this fix (status column, dropped by
# l1f0a2c3d4e5), for downgrade().
_FEATURE_SET_ROW_RULES_PRE_FIX = """
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


def upgrade() -> None:
    op.execute(_FEATURE_SET_ROW_RULES)


def downgrade() -> None:
    op.execute(_FEATURE_SET_ROW_RULES_PRE_FIX)
