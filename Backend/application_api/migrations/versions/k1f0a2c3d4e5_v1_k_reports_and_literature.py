"""current this flow: reports, report_images, literature_reviews, literature_papers"""

from typing import Sequence, Union

from alembic import op


revision: str = "k1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "j1f0a2c3d4e5"
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


# ── Trigger: reports row-level rules ───────────────
_REPORTS_ROW_RULES = """
CREATE OR REPLACE FUNCTION reports_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    run_session uuid;
    lit_session uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'report % is research history and cannot be deleted', OLD.report_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        -- Immutable identity / provenance columns.
        IF NEW.report_id IS DISTINCT FROM OLD.report_id
           OR NEW.session_id IS DISTINCT FROM OLD.session_id
           OR NEW.created_by_agent_run_id IS DISTINCT FROM OLD.created_by_agent_run_id
           OR NEW.literature_review_id IS DISTINCT FROM OLD.literature_review_id
           OR NEW.source_format IS DISTINCT FROM OLD.source_format
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION
                'report % identity and provenance are immutable', OLD.report_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        -- source_version must advance monotonically (A19).
        IF NEW.source_version IS DISTINCT FROM OLD.source_version
           AND NEW.source_version <> OLD.source_version + 1 THEN
            RAISE EXCEPTION
                'report % source_version must increment by 1 (A19); '
                'expected % got %',
                OLD.report_id, OLD.source_version + 1, NEW.source_version
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- R13: exactly one source.  Handled by the CHECK constraint but we
    -- also verify cross-session alignment here.
    IF NEW.created_by_agent_run_id IS NOT NULL THEN
        SELECT session_id INTO run_session
        FROM agent_runs WHERE agent_run_id = NEW.created_by_agent_run_id;
        IF FOUND AND run_session IS DISTINCT FROM NEW.session_id THEN
            RAISE EXCEPTION
                'report % agent run % belongs to session %, not % (R1)',
                NEW.report_id, NEW.created_by_agent_run_id,
                run_session, NEW.session_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;

    IF NEW.literature_review_id IS NOT NULL THEN
        SELECT session_id INTO lit_session
        FROM literature_reviews WHERE literature_review_id = NEW.literature_review_id;
        IF FOUND AND lit_session IS DISTINCT FROM NEW.session_id THEN
            RAISE EXCEPTION
                'report % literature review % belongs to session %, not % (R1)',
                NEW.report_id, NEW.literature_review_id,
                lit_session, NEW.session_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
"""

# ── Trigger: report_images artifact validation ────────────────────────────────
_REPORT_IMAGES_ROW_RULES = """
CREATE OR REPLACE FUNCTION report_images_enforce_row_rules()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    report_session uuid;
    artifact_session uuid;
    artifact_kind text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'report image % cannot be deleted (reports are immutable history)',
            OLD.image_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    SELECT session_id INTO report_session
    FROM reports WHERE report_id = NEW.report_id;

    SELECT session_id, kind INTO artifact_session, artifact_kind
    FROM artifacts WHERE artifact_id = NEW.artifact_id;

    IF artifact_session IS DISTINCT FROM report_session THEN
        RAISE EXCEPTION
            'report_image % artifact % belongs to session %, not % (R1)',
            NEW.image_id, NEW.artifact_id, artifact_session, report_session
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF artifact_kind NOT IN ('figure_png', 'figure_jpeg') THEN
        RAISE EXCEPTION
            'report_image % artifact % has kind %; must be figure_png or figure_jpeg',
            NEW.image_id, NEW.artifact_id, artifact_kind
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    # ── literature_reviews ────────────────────────────────────────────────────
    op.execute(
        f"""
        CREATE TABLE literature_reviews (
            literature_review_id uuid NOT NULL DEFAULT gen_random_uuid(),
            session_id           uuid NOT NULL,
            review_key           text NOT NULL,
            query                jsonb NOT NULL,
            result_summary       jsonb,
            schema_version       integer NOT NULL DEFAULT 1,
            created_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_literature_reviews
                PRIMARY KEY (literature_review_id),
            CONSTRAINT fk_literature_reviews_session_id
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_literature_reviews_session_key
                UNIQUE (session_id, review_key),
            CONSTRAINT ck_literature_reviews_review_key_not_blank
                CHECK (length(btrim(review_key)) > 0),
            CONSTRAINT ck_literature_reviews_schema_version_positive
                CHECK (schema_version >= 1),
            CONSTRAINT ck_literature_reviews_query_versioned
                CHECK ({_versioned('query', nullable=False)}),
            CONSTRAINT ck_literature_reviews_result_summary_versioned
                CHECK ({_versioned('result_summary', nullable=True)})
        )
        """
    )
    op.execute("CREATE INDEX ix_literature_reviews_session_id ON literature_reviews (session_id)")

    # ── literature_papers ─────────────────────────────────────────────────────
    # No created_at.
    # PMID/DOI unique constraints use NULLS NOT DISTINCT, which makes NULL match
    # NULL (one PMID-less paper per review); r1f0a2c3d4e5 makes them plain UNIQUE.
    op.execute(
        """
        CREATE TABLE literature_papers (
            paper_id              uuid NOT NULL DEFAULT gen_random_uuid(),
            literature_review_id  uuid NOT NULL,
            pmid                  text,
            doi                   text,
            title                 text NOT NULL,
            authors               jsonb,
            year                  integer,
            abstract              text,
            journal               text,
            relevance_score       numeric(5,2),
            tier                  text,
            category              text,
            topic                 text,
            similar_study         boolean,
            citation_metadata     jsonb,
            schema_version        integer NOT NULL DEFAULT 1,
            CONSTRAINT pk_literature_papers
                PRIMARY KEY (paper_id),
            CONSTRAINT fk_literature_papers_review_id
                FOREIGN KEY (literature_review_id)
                REFERENCES literature_reviews (literature_review_id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_literature_papers_review_pmid
                UNIQUE NULLS NOT DISTINCT (literature_review_id, pmid),
            CONSTRAINT uq_literature_papers_review_doi
                UNIQUE NULLS NOT DISTINCT (literature_review_id, doi),
            CONSTRAINT ck_literature_papers_title_not_blank
                CHECK (length(btrim(title)) > 0),
            CONSTRAINT ck_literature_papers_has_identifier
                CHECK (pmid IS NOT NULL OR doi IS NOT NULL),
            CONSTRAINT ck_literature_papers_pmid_not_blank
                CHECK (pmid IS NULL OR length(btrim(pmid)) > 0),
            CONSTRAINT ck_literature_papers_doi_not_blank
                CHECK (doi IS NULL OR length(btrim(doi)) > 0),
            CONSTRAINT ck_literature_papers_year_positive
                CHECK (year IS NULL OR year > 0),
            CONSTRAINT ck_literature_papers_relevance_score_bounded
                CHECK (relevance_score IS NULL
                       OR (relevance_score >= 0 AND relevance_score <= 100)),
            CONSTRAINT ck_literature_papers_schema_version_positive
                CHECK (schema_version >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_literature_papers_review_id "
        "ON literature_papers (literature_review_id)"
    )

    # ── reports ───────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE reports (
            report_id                 uuid NOT NULL DEFAULT gen_random_uuid(),
            session_id                uuid NOT NULL,
            created_by_agent_run_id   uuid,
            literature_review_id      uuid,
            source_text               text NOT NULL,
            source_format             text NOT NULL DEFAULT 'latex',
            source_version            integer NOT NULL DEFAULT 1,
            title                     text,
            schema_version            integer NOT NULL DEFAULT 1,
            created_at                timestamptz NOT NULL DEFAULT now(),
            updated_at                timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_reports PRIMARY KEY (report_id),
            CONSTRAINT fk_reports_session_id
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_reports_created_by_agent_run_id
                FOREIGN KEY (created_by_agent_run_id)
                REFERENCES agent_runs (agent_run_id) ON DELETE RESTRICT,
            CONSTRAINT fk_reports_literature_review_id
                FOREIGN KEY (literature_review_id)
                REFERENCES literature_reviews (literature_review_id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_reports_created_by_agent_run_id
                UNIQUE (created_by_agent_run_id),
            CONSTRAINT uq_reports_literature_review_id
                UNIQUE (literature_review_id),
            CONSTRAINT ck_reports_exclusive_source
                CHECK (
                    (created_by_agent_run_id IS NOT NULL) !=
                    (literature_review_id IS NOT NULL)
                ),
            CONSTRAINT ck_reports_source_format
                CHECK (source_format IN ('latex', 'markdown')),
            CONSTRAINT ck_reports_source_text_not_blank
                CHECK (length(btrim(source_text)) > 0),
            CONSTRAINT ck_reports_source_version_positive
                CHECK (source_version >= 1),
            CONSTRAINT ck_reports_schema_version_positive
                CHECK (schema_version >= 1)
        )
        """
    )
    for name, columns in (
        ("ix_reports_session_id", "session_id"),
        ("ix_reports_created_by_agent_run_id", "created_by_agent_run_id"),
        ("ix_reports_literature_review_id", "literature_review_id"),
    ):
        op.execute(f"CREATE INDEX {name} ON reports ({columns})")

    op.execute(_REPORTS_ROW_RULES)
    op.execute(
        "CREATE TRIGGER reports_row_rules "
        "BEFORE INSERT OR UPDATE OR DELETE ON reports "
        "FOR EACH ROW EXECUTE FUNCTION reports_enforce_row_rules()"
    )

    # ── report_images ─────────────────────────────────────────────────────────
    op.execute(
        r"""
        CREATE TABLE report_images (
            image_id    uuid NOT NULL DEFAULT gen_random_uuid(),
            report_id   uuid NOT NULL,
            artifact_id uuid NOT NULL,
            image_key   text NOT NULL,
            CONSTRAINT pk_report_images PRIMARY KEY (image_id),
            CONSTRAINT fk_report_images_report_id
                FOREIGN KEY (report_id) REFERENCES reports (report_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_report_images_artifact_id
                FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_report_images_report_key
                UNIQUE (report_id, image_key),
            CONSTRAINT ck_report_images_image_key_not_blank
                CHECK (length(btrim(image_key)) > 0),
            CONSTRAINT ck_report_images_image_key_is_safe CHECK (
                image_key !~ '^([a-zA-Z]:)?[/\\]'
                AND image_key !~ '(^|[/\\])\.\.[/\\]'
                AND image_key !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://'
            )
        )
        """
    )
    for name, columns in (
        ("ix_report_images_report_id", "report_id"),
        ("ix_report_images_artifact_id", "artifact_id"),
    ):
        op.execute(f"CREATE INDEX {name} ON report_images ({columns})")

    op.execute(_REPORT_IMAGES_ROW_RULES)
    op.execute(
        "CREATE TRIGGER report_images_row_rules "
        "BEFORE INSERT OR DELETE ON report_images "
        "FOR EACH ROW EXECUTE FUNCTION report_images_enforce_row_rules()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS report_images_row_rules ON report_images")
    op.execute("DROP TABLE IF EXISTS report_images")
    op.execute("DROP FUNCTION IF EXISTS report_images_enforce_row_rules()")

    op.execute("DROP TRIGGER IF EXISTS reports_row_rules ON reports")
    op.execute("DROP TABLE IF EXISTS reports")
    op.execute("DROP FUNCTION IF EXISTS reports_enforce_row_rules()")

    op.execute("DROP TABLE IF EXISTS literature_papers")
    op.execute("DROP TABLE IF EXISTS literature_reviews")
