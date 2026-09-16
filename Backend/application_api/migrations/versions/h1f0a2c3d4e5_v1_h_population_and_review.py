"""current this flow: population and segmentation review in PostgreSQL"""

from typing import Sequence, Union

from alembic import op


revision: str = "h1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "g1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors application_api/core/json_bounds.py (DOMAIN_JSON_MAX_BYTES and
# CLINICAL_DATA_MAX_BYTES). The contract test checks they agree.
DOMAIN_JSON_MAX_BYTES = 1024 * 1024
CLINICAL_DATA_MAX_BYTES = 256 * 1024


def _versioned(column: str, *, nullable: bool) -> str:
    rule = (
        f"(jsonb_typeof({column}) = 'object' "
        f"AND jsonb_typeof({column}->'schema_version') = 'number' "
        f"AND pg_column_size({column}) <= {DOMAIN_JSON_MAX_BYTES})"
    )
    return f"({column} IS NULL OR {rule})" if nullable else rule


_COHORT_ROW_RULES = """
CREATE OR REPLACE FUNCTION cohorts_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'cohort % is population provenance and cannot be deleted', OLD.cohort_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.created_by_agent_run_id IS DISTINCT FROM OLD.created_by_agent_run_id
       OR NEW.cohort_key IS DISTINCT FROM OLD.cohort_key
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'cohort % identity is immutable', OLD.cohort_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.frozen_at IS NOT NULL AND (
           NEW.frozen_at IS DISTINCT FROM OLD.frozen_at
        OR NEW.criteria IS DISTINCT FROM OLD.criteria
        OR NEW.label_definition IS DISTINCT FROM OLD.label_definition
        OR NEW.selection_summary IS DISTINCT FROM OLD.selection_summary) THEN
        RAISE EXCEPTION
            'cohort % is frozen; a frozen cohort is immutable (changing it needs a '
            'new session)', OLD.cohort_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""

_MEMBER_ROW_RULES = """
CREATE OR REPLACE FUNCTION cohort_members_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_cohort uuid;
    frozen timestamptz;
    definition jsonb;
BEGIN
    target_cohort := COALESCE(NEW.cohort_id, OLD.cohort_id);
    SELECT frozen_at, label_definition INTO frozen, definition
    FROM cohorts WHERE cohort_id = target_cohort;

    IF frozen IS NOT NULL THEN
        RAISE EXCEPTION
            'cohort % is frozen; its members cannot be inserted, edited or removed',
            target_cohort
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' AND (
           NEW.member_id IS DISTINCT FROM OLD.member_id
        OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
        OR NEW.study_uid IS DISTINCT FROM OLD.study_uid
        OR NEW.volume_series_instance_uid IS DISTINCT FROM OLD.volume_series_instance_uid
        OR NEW.subject_key IS DISTINCT FROM OLD.subject_key
        OR NEW.created_at IS DISTINCT FROM OLD.created_at) THEN
        RAISE EXCEPTION 'cohort member % identity is immutable', OLD.member_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Label validity depends on the cohort's label definition (§2.6 item 18).
    IF NEW.label IS NOT NULL AND definition->>'kind' = 'binary' THEN
        IF jsonb_typeof(NEW.label->(definition->>'field')) IS DISTINCT FROM 'boolean' THEN
            RAISE EXCEPTION
                'cohort member label must carry a boolean %; got %',
                definition->>'field', NEW.label
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

# Shared by segmentation_runs here and by feature_sets / model_results in steps
# I and J: every cohort-backed domain row names a cohort from ITS OWN session,
# and its first insert freezes that cohort.
_COHORT_ALIGNMENT = """
CREATE OR REPLACE FUNCTION domain_rows_enforce_cohort_alignment()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    own_session uuid;
    cohort_session uuid;
BEGIN
    SELECT session_id INTO own_session
    FROM agent_runs WHERE agent_run_id = NEW.created_by_agent_run_id;

    SELECT r.session_id INTO cohort_session
    FROM cohorts c JOIN agent_runs r ON r.agent_run_id = c.created_by_agent_run_id
    WHERE c.cohort_id = NEW.cohort_id;

    IF own_session IS NULL OR cohort_session IS NULL THEN
        -- The plain FKs report the missing parent with the right error.
        RETURN NEW;
    END IF;

    IF own_session <> cohort_session THEN
        RAISE EXCEPTION
            '% row from session % cannot use cohort % from session % (R1)',
            TG_TABLE_NAME, own_session, NEW.cohort_id, cohort_session
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    UPDATE cohorts SET frozen_at = now()
    WHERE cohort_id = NEW.cohort_id AND frozen_at IS NULL;
    RETURN NEW;
END;
$$;
"""

_RUN_ROW_RULES = """
CREATE OR REPLACE FUNCTION segmentation_runs_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    offending integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'segmentation run % is review provenance and cannot be deleted',
            OLD.segmentation_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.segmentation_run_id IS DISTINCT FROM OLD.segmentation_run_id
       OR NEW.created_by_agent_run_id IS DISTINCT FROM OLD.created_by_agent_run_id
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.batch_key IS DISTINCT FROM OLD.batch_key
       OR NEW.config IS DISTINCT FROM OLD.config
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'segmentation run % identity and config are immutable',
            OLD.segmentation_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status = 'submitted' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'segmentation run % review is submitted and final',
            OLD.segmentation_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status IS DISTINCT FROM OLD.review_status AND NOT (
           (OLD.review_status = 'not_ready'  AND NEW.review_status = 'pending')
        OR (OLD.review_status = 'pending'    AND NEW.review_status IN ('not_ready', 'submitting'))
        OR (OLD.review_status = 'submitting' AND NEW.review_status IN ('pending', 'submitted'))
    ) THEN
        RAISE EXCEPTION 'segmentation run % review cannot move from % to %',
            OLD.segmentation_run_id, OLD.review_status, NEW.review_status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status = 'submitting' AND NEW.review_status = 'submitting'
       AND NEW.review_request_hash IS DISTINCT FROM OLD.review_request_hash THEN
        RAISE EXCEPTION
            'segmentation run % is already submitting a different review',
            OLD.segmentation_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status = 'submitted' AND OLD.review_status <> 'submitted' THEN
        -- coalesce(..., false): `review_decision` is NULL for an undecided
        -- pair, and in three-valued logic an un-coalesced OR chain evaluates
        -- to NULL there — which would let exactly the case this check exists
        -- for (a ready pair nobody decided) through.
        SELECT count(*) INTO offending
        FROM segmentation_pairs p
        WHERE p.segmentation_run_id = NEW.segmentation_run_id
          AND coalesce(
                (p.status <> 'ready'
                 AND p.review_decision IS NULL AND p.selected_segmentation_uid IS NULL)
             OR (p.status = 'ready' AND p.review_decision = 'rejected'
                 AND p.selected_segmentation_uid IS NULL)
             OR (p.status = 'ready' AND p.review_decision = 'modified'
                 AND p.selected_segmentation_uid IS NOT NULL)
             OR (p.status = 'ready' AND p.review_decision = 'accepted'
                 AND p.selected_segmentation_uid IS NOT DISTINCT FROM p.candidate_segmentation_uid),
                false
          ) IS FALSE;
        IF offending > 0 THEN
            RAISE EXCEPTION
                'segmentation run % cannot be submitted: % pair(s) lack a final '
                'decision consistent with their candidate and selected SEG',
                NEW.segmentation_run_id, offending
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

_TARGET_ROW_RULES = """
CREATE OR REPLACE FUNCTION segmentation_targets_enforce_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'segmentation target % is part of its batch definition and immutable',
        OLD.target_id
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

_PAIR_ROW_RULES = """
CREATE OR REPLACE FUNCTION segmentation_pairs_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    run_review text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'segmentation pair % is review provenance and cannot be deleted',
            OLD.pair_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    SELECT review_status INTO run_review
    FROM segmentation_runs WHERE segmentation_run_id = NEW.segmentation_run_id;
    IF run_review = 'submitted' THEN
        RAISE EXCEPTION
            'segmentation run % review is submitted; its pairs are final',
            NEW.segmentation_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;

    IF NEW.pair_id IS DISTINCT FROM OLD.pair_id
       OR NEW.segmentation_run_id IS DISTINCT FROM OLD.segmentation_run_id
       OR NEW.target_id IS DISTINCT FROM OLD.target_id
       OR NEW.volume_series_instance_uid IS DISTINCT FROM OLD.volume_series_instance_uid
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.cohort_member_id IS DISTINCT FROM OLD.cohort_member_id
       OR NEW.segment_number IS DISTINCT FROM OLD.segment_number
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'segmentation pair % identity is immutable', OLD.pair_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- The MONAI receipt pointer is content-derived: it may be recorded once,
    -- never re-pointed at a different receipt (R15).
    IF OLD.external_job_id IS NOT NULL
       AND NEW.external_job_id IS DISTINCT FROM OLD.external_job_id THEN
        RAISE EXCEPTION
            'segmentation pair % already reconciles against MONAI job %',
            OLD.pair_id, OLD.external_job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Supporting tuple on the archive: a pair's candidate/selected SEG must be
    # a mask OF THE PAIR'S VOLUME, which is a composite FK.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE segmentations ADD CONSTRAINT "
        "uq_segmentations_label_series_instance_uid "
        "UNIQUE (label_series_instance_uid, volume_series_instance_uid)"
    )

    # ------------------------------------------------------------------
    # cohorts
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE cohorts (
            cohort_id uuid NOT NULL DEFAULT gen_random_uuid(),
            created_by_agent_run_id uuid NOT NULL,
            cohort_key text NOT NULL,
            criteria jsonb NOT NULL,
            label_definition jsonb NOT NULL,
            selection_summary jsonb,
            frozen_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_cohorts PRIMARY KEY (cohort_id),
            CONSTRAINT uq_cohorts_created_by_agent_run_id
                UNIQUE (created_by_agent_run_id, cohort_key),
            CONSTRAINT fk_cohorts_created_by_agent_run_id_agent_runs
                FOREIGN KEY (created_by_agent_run_id)
                REFERENCES agent_runs (agent_run_id) ON DELETE RESTRICT,
            CONSTRAINT ck_cohorts_cohort_key_not_blank CHECK (length(btrim(cohort_key)) > 0),
            CONSTRAINT ck_cohorts_criteria_versioned CHECK ({_versioned('criteria', nullable=False)}),
            CONSTRAINT ck_cohorts_label_definition_versioned
                CHECK ({_versioned('label_definition', nullable=False)}),
            CONSTRAINT ck_cohorts_selection_summary_versioned
                CHECK ({_versioned('selection_summary', nullable=True)})
        )
        """
    )
    op.execute(_COHORT_ROW_RULES)
    op.execute(
        "CREATE TRIGGER cohorts_row_rules BEFORE UPDATE OR DELETE ON cohorts "
        "FOR EACH ROW EXECUTE FUNCTION cohorts_enforce_row_rules()"
    )

    # ------------------------------------------------------------------
    # cohort_members
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE cohort_members (
            member_id uuid NOT NULL DEFAULT gen_random_uuid(),
            cohort_id uuid NOT NULL,
            study_uid text NOT NULL,
            volume_series_instance_uid text NOT NULL,
            subject_key text NOT NULL,
            ordinal integer NOT NULL,
            label jsonb,
            label_source text NOT NULL DEFAULT 'pending',
            label_confidence text,
            label_provenance jsonb,
            clinical_data jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_cohort_members PRIMARY KEY (member_id),
            CONSTRAINT fk_cohort_members_cohort_id_cohorts
                FOREIGN KEY (cohort_id) REFERENCES cohorts (cohort_id) ON DELETE RESTRICT,
            CONSTRAINT fk_cohort_members_study_uid_volume_series
                FOREIGN KEY (study_uid, volume_series_instance_uid)
                REFERENCES volume_series (study_uid, volume_series_instance_uid)
                ON DELETE RESTRICT,
            CONSTRAINT uq_cohort_members_cohort_id
                UNIQUE (cohort_id, volume_series_instance_uid),
            -- DEFERRABLE so ordinals can be compacted inside one transaction.
            CONSTRAINT uq_cohort_members_cohort_id_ordinal
                UNIQUE (cohort_id, ordinal) DEFERRABLE INITIALLY IMMEDIATE,
            -- R1: the tuple segmentation_pairs' member FK targets.
            CONSTRAINT uq_cohort_members_cohort_id_member_id
                UNIQUE (cohort_id, member_id, volume_series_instance_uid),
            CONSTRAINT ck_cohort_members_ordinal_positive CHECK (ordinal > 0),
            CONSTRAINT ck_cohort_members_subject_key_not_blank
                CHECK (length(btrim(subject_key)) > 0),
            -- A11: manual override is a SOURCE, not a confidence value.
            CONSTRAINT ck_cohort_members_label_source CHECK (label_source IN (
                'pending', 'llm_extraction', 'manual_override', 'uploaded_targets'
            )),
            CONSTRAINT ck_cohort_members_label_confidence CHECK (
                label_confidence IS NULL
                OR label_confidence IN ('high', 'medium', 'low', 'unlabeled')
            ),
            CONSTRAINT ck_cohort_members_pending_has_no_label
                CHECK (label_source <> 'pending' OR label IS NULL),
            CONSTRAINT ck_cohort_members_label_is_object
                CHECK (label IS NULL OR jsonb_typeof(label) = 'object'),
            CONSTRAINT ck_cohort_members_label_provenance_bounded CHECK (
                label_provenance IS NULL OR (
                    jsonb_typeof(label_provenance) = 'object'
                    AND pg_column_size(label_provenance) <= {DOMAIN_JSON_MAX_BYTES})
            ),
            CONSTRAINT ck_cohort_members_clinical_data_bounded CHECK (
                clinical_data IS NULL OR (
                    jsonb_typeof(clinical_data) = 'object'
                    AND pg_column_size(clinical_data) <= {CLINICAL_DATA_MAX_BYTES})
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_cohort_members_study_uid_volume_series_instance_uid "
        "ON cohort_members (study_uid, volume_series_instance_uid)"
    )
    op.execute(_MEMBER_ROW_RULES)
    op.execute(
        "CREATE TRIGGER cohort_members_row_rules "
        "BEFORE INSERT OR UPDATE OR DELETE ON cohort_members "
        "FOR EACH ROW EXECUTE FUNCTION cohort_members_enforce_row_rules()"
    )

    op.execute(_COHORT_ALIGNMENT)

    # ------------------------------------------------------------------
    # segmentation_runs
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE segmentation_runs (
            segmentation_run_id uuid NOT NULL DEFAULT gen_random_uuid(),
            created_by_agent_run_id uuid NOT NULL,
            cohort_id uuid NOT NULL,
            batch_key text NOT NULL,
            config jsonb NOT NULL,
            review_status text NOT NULL DEFAULT 'not_ready',
            review_request_hash text,
            reviewed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_segmentation_runs PRIMARY KEY (segmentation_run_id),
            CONSTRAINT uq_segmentation_runs_created_by_agent_run_id
                UNIQUE (created_by_agent_run_id, batch_key),
            -- R1: restored supporting tuple the pairs' composite FK targets.
            CONSTRAINT uq_segmentation_runs_segmentation_run_id
                UNIQUE (segmentation_run_id, cohort_id),
            CONSTRAINT fk_segmentation_runs_created_by_agent_run_id_agent_runs
                FOREIGN KEY (created_by_agent_run_id)
                REFERENCES agent_runs (agent_run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_segmentation_runs_cohort_id_cohorts
                FOREIGN KEY (cohort_id) REFERENCES cohorts (cohort_id) ON DELETE RESTRICT,
            CONSTRAINT ck_segmentation_runs_batch_key_not_blank
                CHECK (length(btrim(batch_key)) > 0),
            CONSTRAINT ck_segmentation_runs_config_versioned
                CHECK ({_versioned('config', nullable=False)}),
            CONSTRAINT ck_segmentation_runs_review_status CHECK (
                review_status IN ('not_ready', 'pending', 'submitting', 'submitted')
            ),
            CONSTRAINT ck_segmentation_runs_hash_iff_submitting CHECK (
                (review_status IN ('submitting', 'submitted'))
                = (review_request_hash IS NOT NULL)
            ),
            CONSTRAINT ck_segmentation_runs_reviewed_at_iff_submitted CHECK (
                (review_status = 'submitted') = (reviewed_at IS NOT NULL)
            ),
            CONSTRAINT ck_segmentation_runs_review_request_hash_is_a_digest CHECK (
                review_request_hash IS NULL OR review_request_hash ~ '^[0-9a-f]{{64}}$'
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_segmentation_runs_cohort_id ON segmentation_runs (cohort_id)")
    op.execute(
        "CREATE TRIGGER segmentation_runs_cohort_alignment BEFORE INSERT ON segmentation_runs "
        "FOR EACH ROW EXECUTE FUNCTION domain_rows_enforce_cohort_alignment()"
    )
    op.execute(_RUN_ROW_RULES)
    op.execute(
        "CREATE TRIGGER segmentation_runs_row_rules "
        "BEFORE UPDATE OR DELETE ON segmentation_runs "
        "FOR EACH ROW EXECUTE FUNCTION segmentation_runs_enforce_row_rules()"
    )

    # ------------------------------------------------------------------
    # segmentation_targets. No created_at.
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE segmentation_targets (
            target_id uuid NOT NULL DEFAULT gen_random_uuid(),
            segmentation_run_id uuid NOT NULL,
            target_key text NOT NULL,
            label text NOT NULL,
            specification jsonb NOT NULL,
            model_id uuid,
            CONSTRAINT pk_segmentation_targets PRIMARY KEY (target_id),
            CONSTRAINT uq_segmentation_targets_segmentation_run_id
                UNIQUE (segmentation_run_id, target_key),
            CONSTRAINT uq_segmentation_targets_segmentation_run_id_target_id
                UNIQUE (segmentation_run_id, target_id),
            CONSTRAINT fk_segmentation_targets_segmentation_run_id_segmentation_runs
                FOREIGN KEY (segmentation_run_id)
                REFERENCES segmentation_runs (segmentation_run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_segmentation_targets_model_id_models
                FOREIGN KEY (model_id) REFERENCES models (model_id) ON DELETE RESTRICT,
            CONSTRAINT ck_segmentation_targets_target_key_not_blank
                CHECK (length(btrim(target_key)) > 0),
            CONSTRAINT ck_segmentation_targets_label_not_blank
                CHECK (length(btrim(label)) > 0),
            CONSTRAINT ck_segmentation_targets_specification_versioned
                CHECK ({_versioned('specification', nullable=False)})
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_segmentation_targets_model_id ON segmentation_targets (model_id)"
    )
    op.execute(_TARGET_ROW_RULES)
    op.execute(
        "CREATE TRIGGER segmentation_targets_immutable "
        "BEFORE UPDATE OR DELETE ON segmentation_targets "
        "FOR EACH ROW EXECUTE FUNCTION segmentation_targets_enforce_immutable()"
    )

    # ------------------------------------------------------------------
    # segmentation_pairs
    # ------------------------------------------------------------------
    op.execute(
        f"""
        CREATE TABLE segmentation_pairs (
            pair_id uuid NOT NULL DEFAULT gen_random_uuid(),
            segmentation_run_id uuid NOT NULL,
            target_id uuid NOT NULL,
            volume_series_instance_uid text NOT NULL,
            cohort_id uuid NOT NULL,
            cohort_member_id uuid NOT NULL,
            external_job_id text,
            status text NOT NULL DEFAULT 'pending',
            candidate_segmentation_uid text,
            selected_segmentation_uid text,
            segment_number integer NOT NULL DEFAULT 1,
            review_decision text,
            quality jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_segmentation_pairs PRIMARY KEY (pair_id),
            CONSTRAINT uq_segmentation_pairs_target_id
                UNIQUE (target_id, volume_series_instance_uid),
            -- R1: the target belongs to this pair's batch.
            CONSTRAINT fk_segmentation_pairs_segmentation_run_id_target_id
                FOREIGN KEY (segmentation_run_id, target_id)
                REFERENCES segmentation_targets (segmentation_run_id, target_id)
                ON DELETE RESTRICT,
            -- R1: the pair's cohort is its batch's cohort.
            CONSTRAINT fk_segmentation_pairs_segmentation_run_id_cohort_id
                FOREIGN KEY (segmentation_run_id, cohort_id)
                REFERENCES segmentation_runs (segmentation_run_id, cohort_id)
                ON DELETE RESTRICT,
            -- R1: the member belongs to that cohort AND the pair's volume is
            -- the member's volume.
            CONSTRAINT fk_segmentation_pairs_cohort_member
                FOREIGN KEY (cohort_id, cohort_member_id, volume_series_instance_uid)
                REFERENCES cohort_members (cohort_id, member_id, volume_series_instance_uid)
                ON DELETE RESTRICT,
            -- Candidate and selected SEGs are masks of the pair's own volume.
            CONSTRAINT fk_segmentation_pairs_candidate_segmentation
                FOREIGN KEY (candidate_segmentation_uid, volume_series_instance_uid)
                REFERENCES segmentations (label_series_instance_uid, volume_series_instance_uid)
                ON DELETE RESTRICT,
            CONSTRAINT fk_segmentation_pairs_selected_segmentation
                FOREIGN KEY (selected_segmentation_uid, volume_series_instance_uid)
                REFERENCES segmentations (label_series_instance_uid, volume_series_instance_uid)
                ON DELETE RESTRICT,
            CONSTRAINT ck_segmentation_pairs_status
                CHECK (status IN ('pending', 'running', 'ready', 'failed')),
            CONSTRAINT ck_segmentation_pairs_segment_number_positive
                CHECK (segment_number > 0),
            CONSTRAINT ck_segmentation_pairs_review_decision CHECK (
                review_decision IS NULL
                OR review_decision IN ('accepted', 'modified', 'rejected')
            ),
            -- Rejected requires no selected output; a selected output implies
            -- a positive decision.
            CONSTRAINT ck_segmentation_pairs_selected_implies_positive_decision CHECK (
                selected_segmentation_uid IS NULL
                OR review_decision IN ('accepted', 'modified')
            ),
            CONSTRAINT ck_segmentation_pairs_external_job_id_not_blank CHECK (
                external_job_id IS NULL OR length(btrim(external_job_id)) > 0
            ),
            CONSTRAINT ck_segmentation_pairs_quality_versioned
                CHECK ({_versioned('quality', nullable=True)})
        )
        """
    )
    for name, columns in (
        ("ix_segmentation_pairs_segmentation_run_id_status", "segmentation_run_id, status"),
        ("ix_segmentation_pairs_cohort_id_cohort_member_id", "cohort_id, cohort_member_id"),
        ("ix_segmentation_pairs_candidate_segmentation_uid", "candidate_segmentation_uid"),
        ("ix_segmentation_pairs_selected_segmentation_uid", "selected_segmentation_uid"),
    ):
        op.execute(f"CREATE INDEX {name} ON segmentation_pairs ({columns})")
    op.execute(_PAIR_ROW_RULES)
    op.execute(
        "CREATE TRIGGER segmentation_pairs_row_rules "
        "BEFORE INSERT OR UPDATE OR DELETE ON segmentation_pairs "
        "FOR EACH ROW EXECUTE FUNCTION segmentation_pairs_enforce_row_rules()"
    )


def downgrade() -> None:
    for trigger, table in (
        ("segmentation_pairs_row_rules", "segmentation_pairs"),
        ("segmentation_targets_immutable", "segmentation_targets"),
        ("segmentation_runs_row_rules", "segmentation_runs"),
        ("segmentation_runs_cohort_alignment", "segmentation_runs"),
        ("cohort_members_row_rules", "cohort_members"),
        ("cohorts_row_rules", "cohorts"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("DROP TABLE IF EXISTS segmentation_pairs")
    op.execute("DROP TABLE IF EXISTS segmentation_targets")
    op.execute("DROP TABLE IF EXISTS segmentation_runs")
    op.execute("DROP TABLE IF EXISTS cohort_members")
    op.execute("DROP TABLE IF EXISTS cohorts")
    for function in (
        "segmentation_pairs_enforce_row_rules()",
        "segmentation_targets_enforce_immutable()",
        "segmentation_runs_enforce_row_rules()",
        "domain_rows_enforce_cohort_alignment()",
        "cohort_members_enforce_row_rules()",
        "cohorts_enforce_row_rules()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    op.execute(
        "ALTER TABLE segmentations DROP CONSTRAINT IF EXISTS "
        "uq_segmentations_label_series_instance_uid"
    )
