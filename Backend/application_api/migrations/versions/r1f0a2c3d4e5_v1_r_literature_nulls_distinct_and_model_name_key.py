"""literature paper keys treat NULLs as distinct; drop the duplicate model-name key

1. `uq_literature_papers_review_pmid` and `uq_literature_papers_review_doi` were
   created `UNIQUE NULLS NOT DISTINCT`, which makes NULL equal to NULL: a review
   could hold only one paper without a PMID and only one without a DOI, so
   saving a second DOI-only (or PMID-only) paper failed the whole batch with a
   409. They become plain `UNIQUE`, where NULLs are distinct, which is what
   the model and the deduplication in the literature repository expect.
2. `inference_models.name` carried two identical unique constraints:
   `inference_models_model_name_key` (from the original table) and
   `uq_inference_models_name` (added later under the naming convention). The
   older one is dropped; the upsert's `ON CONFLICT (name)` still finds the
   other.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "r1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "q1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE literature_papers
            DROP CONSTRAINT uq_literature_papers_review_pmid,
            DROP CONSTRAINT uq_literature_papers_review_doi,
            ADD CONSTRAINT uq_literature_papers_review_pmid
                UNIQUE (literature_review_id, pmid),
            ADD CONSTRAINT uq_literature_papers_review_doi
                UNIQUE (literature_review_id, doi)
        """
    )
    op.execute(
        "ALTER TABLE inference_models DROP CONSTRAINT IF EXISTS inference_models_model_name_key"
    )


def downgrade() -> None:
    # Restoring NULLS NOT DISTINCT fails if a review now holds more than one
    # paper without a PMID (or without a DOI); those rows must be resolved first.
    op.execute(
        """
        ALTER TABLE inference_models
            ADD CONSTRAINT inference_models_model_name_key UNIQUE (name)
        """
    )
    op.execute(
        """
        ALTER TABLE literature_papers
            DROP CONSTRAINT uq_literature_papers_review_pmid,
            DROP CONSTRAINT uq_literature_papers_review_doi,
            ADD CONSTRAINT uq_literature_papers_review_pmid
                UNIQUE NULLS NOT DISTINCT (literature_review_id, pmid),
            ADD CONSTRAINT uq_literature_papers_review_doi
                UNIQUE NULLS NOT DISTINCT (literature_review_id, doi)
        """
    )
