"""v1_o: Cut-over 1 -- rename the schema to the Appendix P vocabulary (D20, D26)

Renames only, with no behaviour change (plan.md Phase 6.1, Appendix P section P.9
"Cut-over 1"):

1. the eight tables and every renamed column (FKs, CHECK bodies and index
   expressions follow a rename automatically);
2. every constraint and index whose name embeds a renamed table or column, to
   its NAMING_CONVENTION name, plus the ws_log identity sequence. PostgreSQL's
   own NOT NULL constraint names (`<table>_<column>_not_null`) are not part of
   NAMING_CONVENTION and are left as they are;
3. every trigger function whose PL/pgSQL body names a renamed table or column
   is re-created (bodies are text and do not follow a rename). A function or
   trigger named after a renamed table is renamed too; the triggers keep
   pointing at the same function, so their timing and deferral are unchanged;
4. the `segmentation_batches.config` key `correlation_run_id` becomes
   `batch_correlation_id`;
5. `stored_files.retention_class` values `user_retained`/`system_render` become
   `retained`/`render` (their two CHECKs are dropped first and re-created);
6. historical `session_events.payload` rows are not touched;
7. downgrade() reverses every step, restoring the n1 function bodies verbatim.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "o1f0a2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "n1f0a2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_RENAMES = [
    ("plans", "session_graphs"),
    ("plan_steps", "graph_nodes"),
    ("agent_runs", "node_runs"),
    ("ws_log", "session_events"),
    ("artifacts", "stored_files"),
    ("segmentation_runs", "segmentation_batches"),
    ("segmentations", "segmentation_masks"),
    ("models", "inference_models"),
]

# (table after the table renames, old column, new column)
COLUMN_RENAMES = [
    ("graph_nodes", "step_id", "node_id"),
    ("graph_nodes", "step_order", "position"),
    ("graph_nodes", "agent_type", "target_name"),
    ("graph_nodes", "instructions", "params"),
    ("node_runs", "agent_run_id", "run_id"),
    ("node_runs", "step_id", "node_id"),
    ("node_runs", "input_refs", "resolved_inputs"),
    ("node_runs", "durable_notes", "scratchpad_notes"),
    ("node_runs", "warnings", "scratchpad_warnings"),
    ("node_runs", "output_summary", "scratchpad_summary"),
    ("session_events", "msg_type", "event_type"),
    ("stored_files", "artifact_id", "file_id"),
    ("stored_files", "created_by_agent_run_id", "created_by_run_id"),
    ("model_results", "predictions_artifact_id", "predictions_file_id"),
    ("model_results", "input_artifact_id", "input_file_id"),
    ("model_results", "created_by_agent_run_id", "created_by_run_id"),
    ("report_images", "artifact_id", "file_id"),
    ("segmentation_batches", "segmentation_run_id", "segmentation_batch_id"),
    ("segmentation_batches", "created_by_agent_run_id", "created_by_run_id"),
    ("segmentation_targets", "segmentation_run_id", "segmentation_batch_id"),
    ("segmentation_targets", "model_id", "inference_model_id"),
    ("segmentation_pairs", "segmentation_run_id", "segmentation_batch_id"),
    ("segmentation_pairs", "external_job_id", "inference_job_id"),
    ("segmentation_masks", "model_id", "inference_model_id"),
    ("inference_models", "model_id", "inference_model_id"),
    ("sessions", "status", "control_state"),
    ("chats", "last_activity", "last_activity_at"),
    ("chat_messages", "timestamp", "created_at"),
    ("chat_messages", "extra_data", "metadata"),
    ("radiomics_features", "id", "radiomics_feature_id"),
    ("radiomics_features", "created_by_agent_run_id", "created_by_run_id"),
    ("cohorts", "created_by_agent_run_id", "created_by_run_id"),
    ("feature_sets", "created_by_agent_run_id", "created_by_run_id"),
    ("reports", "created_by_agent_run_id", "created_by_run_id"),
]

# (table after the table renames, old name, new name). PRIMARY KEY and UNIQUE
# constraints rename their backing index with them.
CONSTRAINT_RENAMES = [
    ("node_runs", "ck_agent_runs_compiled_before_start", "ck_node_runs_compiled_before_start"),
    ("node_runs", "ck_agent_runs_compiled_prompt_bounded", "ck_node_runs_compiled_prompt_bounded"),
    (
        "node_runs",
        "ck_agent_runs_completed_at_iff_terminal",
        "ck_node_runs_completed_at_iff_terminal",
    ),
    ("node_runs", "ck_agent_runs_failed_has_error", "ck_node_runs_failed_has_error"),
    ("node_runs", "ck_agent_runs_json_bounded", "ck_node_runs_json_bounded"),
    ("node_runs", "ck_agent_runs_started_iff_not_pending", "ck_node_runs_started_iff_not_pending"),
    ("node_runs", "ck_agent_runs_status", "ck_node_runs_status"),
    (
        "node_runs",
        "fk_agent_runs_step_id_session_id_plan_steps",
        "fk_node_runs_node_id_session_id_graph_nodes",
    ),
    ("node_runs", "pk_agent_runs", "pk_node_runs"),
    ("node_runs", "uq_agent_runs_agent_run_id", "uq_node_runs_run_id"),
    ("node_runs", "uq_agent_runs_step_id", "uq_node_runs_node_id"),
    (
        "stored_files",
        "ck_artifacts_body_matches_byte_size",
        "ck_stored_files_body_matches_byte_size",
    ),
    ("stored_files", "ck_artifacts_byte_size_positive", "ck_stored_files_byte_size_positive"),
    (
        "stored_files",
        "ck_artifacts_display_filename_not_blank",
        "ck_stored_files_display_filename_not_blank",
    ),
    ("stored_files", "ck_artifacts_kind", "ck_stored_files_kind"),
    ("stored_files", "ck_artifacts_logical_key_not_blank", "ck_stored_files_logical_key_not_blank"),
    ("stored_files", "ck_artifacts_metadata_bounded", "ck_stored_files_metadata_bounded"),
    ("stored_files", "ck_artifacts_mime_type_not_blank", "ck_stored_files_mime_type_not_blank"),
    (
        "stored_files",
        "ck_artifacts_render_path_is_relative",
        "ck_stored_files_render_path_is_relative",
    ),
    ("stored_files", "ck_artifacts_sha256_is_a_digest", "ck_stored_files_sha256_is_a_digest"),
    (
        "stored_files",
        "fk_artifacts_created_by_agent_run_id_session_id_agent_runs",
        "fk_stored_files_created_by_run_id_session_id_node_runs",
    ),
    ("stored_files", "fk_artifacts_session_id_sessions", "fk_stored_files_session_id_sessions"),
    ("stored_files", "pk_artifacts", "pk_stored_files"),
    ("stored_files", "uq_artifacts_session_id", "uq_stored_files_session_id"),
    (
        "cohorts",
        "fk_cohorts_created_by_agent_run_id_agent_runs",
        "fk_cohorts_created_by_run_id_node_runs",
    ),
    ("cohorts", "uq_cohorts_created_by_agent_run_id", "uq_cohorts_created_by_run_id"),
    (
        "feature_sets",
        "fk_feature_sets_created_by_agent_run_id_agent_runs",
        "fk_feature_sets_created_by_run_id_node_runs",
    ),
    (
        "feature_sets",
        "uq_feature_sets_created_by_agent_run_id",
        "uq_feature_sets_created_by_run_id",
    ),
    (
        "model_results",
        "fk_model_results_created_by_agent_run_id_agent_runs",
        "fk_model_results_created_by_run_id_node_runs",
    ),
    (
        "model_results",
        "fk_model_results_input_artifact_id_artifacts",
        "fk_model_results_input_file_id_stored_files",
    ),
    (
        "model_results",
        "fk_model_results_predictions_artifact_id_artifacts",
        "fk_model_results_predictions_file_id_stored_files",
    ),
    (
        "model_results",
        "uq_model_results_created_by_agent_run_id",
        "uq_model_results_created_by_run_id",
    ),
    ("inference_models", "models_configuration_check", "inference_models_configuration_check"),
    ("inference_models", "models_model_name_key", "inference_models_model_name_key"),
    ("inference_models", "models_pkey", "pk_inference_models"),
    ("inference_models", "uq_models_name", "uq_inference_models_name"),
    ("graph_nodes", "ck_plan_steps_step_order_positive", "ck_graph_nodes_position_positive"),
    ("graph_nodes", "fk_plan_steps_session_id_plans", "fk_graph_nodes_session_id_session_graphs"),
    ("graph_nodes", "pk_plan_steps", "pk_graph_nodes"),
    ("graph_nodes", "uq_plan_steps_session_id", "uq_graph_nodes_session_id"),
    ("graph_nodes", "uq_plan_steps_step_id", "uq_graph_nodes_node_id"),
    ("session_graphs", "fk_plans_session_id_sessions", "fk_session_graphs_session_id_sessions"),
    ("session_graphs", "pk_plans", "pk_session_graphs"),
    (
        "radiomics_features",
        "fk_radiomics_features_created_by_agent_run_id_agent_runs",
        "fk_radiomics_features_created_by_run_id_node_runs",
    ),
    (
        "radiomics_features",
        "fk_radiomics_features_created_by_agent_run_id_session_id",
        "fk_radiomics_features_created_by_run_id_session_id",
    ),
    (
        "radiomics_features",
        "fk_radiomics_features_label_series_instance_uid_segmentations",
        "fk_radiomics_features_label_series_instance_uid_segmentation_masks",
    ),
    ("report_images", "fk_report_images_artifact_id", "fk_report_images_file_id"),
    ("reports", "fk_reports_created_by_agent_run_id", "fk_reports_created_by_run_id"),
    ("reports", "uq_reports_created_by_agent_run_id", "uq_reports_created_by_run_id"),
    (
        "segmentation_pairs",
        "ck_segmentation_pairs_external_job_id_not_blank",
        "ck_segmentation_pairs_inference_job_id_not_blank",
    ),
    (
        "segmentation_pairs",
        "fk_segmentation_pairs_segmentation_run_id_cohort_id",
        "fk_segmentation_pairs_segmentation_batch_id_cohort_id",
    ),
    (
        "segmentation_pairs",
        "fk_segmentation_pairs_segmentation_run_id_target_id",
        "fk_segmentation_pairs_segmentation_batch_id_target_id",
    ),
    (
        "segmentation_batches",
        "ck_segmentation_runs_batch_key_not_blank",
        "ck_segmentation_batches_batch_key_not_blank",
    ),
    (
        "segmentation_batches",
        "ck_segmentation_runs_config_versioned",
        "ck_segmentation_batches_config_versioned",
    ),
    (
        "segmentation_batches",
        "ck_segmentation_runs_hash_iff_submitting",
        "ck_segmentation_batches_hash_iff_submitting",
    ),
    (
        "segmentation_batches",
        "ck_segmentation_runs_review_request_hash_is_a_digest",
        "ck_segmentation_batches_review_request_hash_is_a_digest",
    ),
    (
        "segmentation_batches",
        "ck_segmentation_runs_review_status",
        "ck_segmentation_batches_review_status",
    ),
    (
        "segmentation_batches",
        "ck_segmentation_runs_reviewed_at_iff_submitted",
        "ck_segmentation_batches_reviewed_at_iff_submitted",
    ),
    (
        "segmentation_batches",
        "fk_segmentation_runs_cohort_id_cohorts",
        "fk_segmentation_batches_cohort_id_cohorts",
    ),
    (
        "segmentation_batches",
        "fk_segmentation_runs_created_by_agent_run_id_agent_runs",
        "fk_segmentation_batches_created_by_run_id_node_runs",
    ),
    ("segmentation_batches", "pk_segmentation_runs", "pk_segmentation_batches"),
    (
        "segmentation_batches",
        "uq_segmentation_runs_created_by_agent_run_id",
        "uq_segmentation_batches_created_by_run_id",
    ),
    (
        "segmentation_batches",
        "uq_segmentation_runs_segmentation_run_id",
        "uq_segmentation_batches_segmentation_batch_id",
    ),
    (
        "segmentation_targets",
        "fk_segmentation_targets_model_id_models",
        "fk_segmentation_targets_inference_model_id_inference_models",
    ),
    (
        "segmentation_targets",
        "fk_segmentation_targets_segmentation_run_id_segmentation_runs",
        "fk_segmentation_targets_segmentation_batch_id_segmentation_batches",
    ),
    (
        "segmentation_targets",
        "uq_segmentation_targets_segmentation_run_id_target_id",
        "uq_segmentation_targets_segmentation_batch_id_target_id",
    ),
    (
        "segmentation_targets",
        "uq_segmentation_targets_segmentation_run_id",
        "uq_segmentation_targets_segmentation_batch_id",
    ),
    (
        "segmentation_masks",
        "ck_segmentations_archive_status",
        "ck_segmentation_masks_archive_status",
    ),
    ("segmentation_masks", "ck_segmentations_origin", "ck_segmentation_masks_origin"),
    (
        "segmentation_masks",
        "fk_segmentations_model_id_models",
        "fk_segmentation_masks_inference_model_id_inference_models",
    ),
    (
        "segmentation_masks",
        "fk_segmentations_volume_series_instance_uid_volume_series",
        "fk_segmentation_masks_volume_series_instance_uid_volume_series",
    ),
    ("segmentation_masks", "segmentations_pkey", "pk_segmentation_masks"),
    (
        "segmentation_masks",
        "uq_segmentations_label_series_instance_uid",
        "uq_segmentation_masks_label_series_instance_uid",
    ),
    ("sessions", "ck_sessions_status", "ck_sessions_control_state"),
    ("session_events", "ck_ws_log_msg_type_not_blank", "ck_session_events_event_type_not_blank"),
    ("session_events", "ck_ws_log_payload_bounded", "ck_session_events_payload_bounded"),
    ("session_events", "fk_ws_log_session_id_sessions", "fk_session_events_session_id_sessions"),
    ("session_events", "pk_ws_log", "pk_session_events"),
]

# Indexes that back no constraint.
INDEX_RENAMES = [
    ("ix_agent_runs_session_id_status", "ix_node_runs_session_id_status"),
    ("ix_artifacts_created_by_agent_run_id", "ix_stored_files_created_by_run_id"),
    ("ix_artifacts_session_id_kind", "ix_stored_files_session_id_kind"),
    ("ix_chat_messages_chat_id_timestamp", "ix_chat_messages_chat_id_created_at"),
    ("ix_chats_last_activity", "ix_chats_last_activity_at"),
    ("ix_model_results_input_artifact_id", "ix_model_results_input_file_id"),
    ("ix_model_results_predictions_artifact_id", "ix_model_results_predictions_file_id"),
    ("ix_plan_steps_session_id_step_order", "ix_graph_nodes_session_id_position"),
    ("ix_radiomics_features_created_by_agent_run_id", "ix_radiomics_features_created_by_run_id"),
    ("ix_report_images_artifact_id", "ix_report_images_file_id"),
    (
        "ix_segmentation_pairs_segmentation_run_id_status",
        "ix_segmentation_pairs_segmentation_batch_id_status",
    ),
    ("ix_segmentation_runs_cohort_id", "ix_segmentation_batches_cohort_id"),
    ("ix_segmentation_targets_model_id", "ix_segmentation_targets_inference_model_id"),
    ("idx_segmentations_series_method", "idx_segmentation_masks_series_method"),
    ("ix_segmentations_archive_status", "ix_segmentation_masks_archive_status"),
    ("ix_segmentations_model_id", "ix_segmentation_masks_inference_model_id"),
    ("ix_segmentations_orthanc_series_id", "ix_segmentation_masks_orthanc_series_id"),
    ("ix_segmentations_target_label", "ix_segmentation_masks_target_label"),
    ("ix_sessions_status", "ix_sessions_control_state"),
    ("ix_ws_log_session_id_event_id", "ix_session_events_session_id_event_id"),
]

SEQUENCE_RENAMES = [
    ("ws_log_event_id_seq", "session_events_event_id_seq"),
]

# (table after the table renames, old trigger, new trigger, constraint trigger?)
# A constraint trigger also has a pg_constraint row of the same name, which
# ALTER TRIGGER does not rename.
TRIGGER_RENAMES = [
    ("node_runs", "agent_runs_row_rules", "node_runs_row_rules", False),
    ("node_runs", "agent_runs_run_cardinality", "node_runs_run_cardinality", True),
    ("graph_nodes", "plan_steps_run_cardinality", "graph_nodes_run_cardinality", True),
    ("graph_nodes", "plan_steps_frozen_after_approval", "graph_nodes_frozen_after_approval", False),
    ("session_graphs", "plans_run_cardinality", "session_graphs_run_cardinality", True),
    ("session_graphs", "plans_approval_is_one_way", "session_graphs_approval_is_one_way", False),
    ("session_graphs", "plans_not_deletable_alone", "session_graphs_not_deletable_alone", False),
    ("sessions", "sessions_must_have_a_plan", "sessions_must_have_a_session_graph", True),
    ("stored_files", "artifacts_bytes_are_immutable", "stored_files_bytes_are_immutable", False),
    (
        "segmentation_batches",
        "segmentation_runs_cohort_alignment",
        "segmentation_batches_cohort_alignment",
        False,
    ),
    (
        "segmentation_batches",
        "segmentation_runs_row_rules",
        "segmentation_batches_row_rules",
        False,
    ),
]

FUNCTION_RENAMES = [
    ("agent_runs_enforce_row_rules", "node_runs_enforce_row_rules"),
    ("plans_enforce_run_cardinality", "session_graphs_enforce_run_cardinality"),
    ("plan_steps_reject_approved_mutation", "graph_nodes_reject_approved_mutation"),
    ("sessions_require_a_plan", "sessions_require_a_session_graph"),
    ("plans_reject_orphan_delete", "session_graphs_reject_orphan_delete"),
    ("plans_reject_unapproval", "session_graphs_reject_unapproval"),
    ("artifacts_enforce_immutable_bytes", "stored_files_enforce_immutable_bytes"),
    ("segmentation_runs_enforce_row_rules", "segmentation_batches_enforce_row_rules"),
]

# Every function whose body names a renamed table or column, re-created under
# its (possibly new) name with the new names.
_FUNCTIONS_O = r"""
CREATE OR REPLACE FUNCTION public.node_runs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'run % is execution provenance and cannot be deleted',
            OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.node_id IS DISTINCT FROM OLD.node_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id THEN
        RAISE EXCEPTION 'run % identity is immutable', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'completed' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'run % is completed and immutable', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.started_at IS NOT NULL AND (
           NEW.started_at IS DISTINCT FROM OLD.started_at
        OR NEW.compiled_prompt IS DISTINCT FROM OLD.compiled_prompt
        OR NEW.resolved_inputs IS DISTINCT FROM OLD.resolved_inputs) THEN
        RAISE EXCEPTION
            'run % compiled_prompt/resolved_inputs/started_at are immutable once '
            'the run has started', OLD.run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'pending' AND NEW.status = 'running')
        OR (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
        OR (OLD.status = 'failed'  AND NEW.status = 'running')
    ) THEN
        RAISE EXCEPTION 'run % cannot move from % to %',
            OLD.run_id, OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.session_graphs_enforce_run_cardinality()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    target uuid;
    approved timestamptz;
    node_count integer;
    run_count integer;
    matched integer;
BEGIN
    target := COALESCE(NEW.session_id, OLD.session_id);
    SELECT approved_at INTO approved FROM session_graphs WHERE session_id = target;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT count(*) INTO run_count FROM node_runs WHERE session_id = target;

    IF approved IS NULL THEN
        IF run_count > 0 THEN
            RAISE EXCEPTION
                'draft session graph for session % has % run(s); a draft has none '
                '(A21)', target, run_count
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NULL;
    END IF;

    SELECT count(*) INTO node_count FROM graph_nodes WHERE session_id = target;
    SELECT count(*) INTO matched
    FROM graph_nodes s JOIN node_runs r ON r.node_id = s.node_id
    WHERE s.session_id = target;

    IF node_count = 0 OR run_count <> node_count OR matched <> node_count THEN
        RAISE EXCEPTION
            'approved session graph for session % has % node(s) and % run(s) (% matched); '
            'approval requires exactly one run per node (A21)',
            target, node_count, run_count, matched
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.graph_nodes_reject_approved_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    target uuid;
    approved timestamptz;
BEGIN
    target := COALESCE(NEW.session_id, OLD.session_id);
    SELECT approved_at INTO approved FROM session_graphs WHERE session_id = target;
    IF approved IS NOT NULL THEN
        RAISE EXCEPTION
            'session graph for session % was approved at %; its nodes are immutable',
            target, approved
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;

CREATE OR REPLACE FUNCTION public.sessions_require_a_session_graph()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM session_graphs WHERE session_id = NEW.session_id) THEN
        RAISE EXCEPTION
            'session % was committed with no session graph; create the session and its'
            ' empty session graph in one transaction', NEW.session_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.session_graphs_reject_orphan_delete()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF EXISTS (SELECT 1 FROM sessions WHERE session_id = OLD.session_id) THEN
        RAISE EXCEPTION
            'session graph % cannot be deleted while its session exists', OLD.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$function$;

CREATE OR REPLACE FUNCTION public.session_graphs_reject_unapproval()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.approved_at IS NOT NULL
       AND NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
        RAISE EXCEPTION
            'session graph approval is one-way (session %)', OLD.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.stored_files_enforce_immutable_bytes()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.file_id IS DISTINCT FROM OLD.file_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.logical_key IS DISTINCT FROM OLD.logical_key
       OR NEW.retention_class IS DISTINCT FROM OLD.retention_class THEN
        RAISE EXCEPTION
            'stored file % identity is immutable', OLD.file_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.retention_class = 'retained' AND (
           NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
        OR NEW.body IS DISTINCT FROM OLD.body
    ) THEN
        RAISE EXCEPTION
            'stored file % holds retained bytes; they are immutable. Publish a new '
            'logical_key instead of replacing them', OLD.file_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- A13: a render row catalogues a REGENERABLE render. Its digest is
    -- the last-seen content, not a promise -- editing a mask legitimately
    -- produces a new GIF under the same key. The render PATH is still fixed,
    -- so the row cannot be re-pointed at some other file.
    IF OLD.retention_class = 'render'
       AND NEW.render_path IS DISTINCT FROM OLD.render_path THEN
        RAISE EXCEPTION
            'stored file % render_path is immutable; catalogue a new key for a '
            'different render', OLD.file_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.segmentation_batches_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    offending integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'segmentation batch % is review provenance and cannot be deleted',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.segmentation_batch_id IS DISTINCT FROM OLD.segmentation_batch_id
       OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id
       OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.batch_key IS DISTINCT FROM OLD.batch_key
       OR NEW.config IS DISTINCT FROM OLD.config
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'segmentation batch % identity and config are immutable',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status = 'submitted' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'segmentation batch % review is submitted and final',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status IS DISTINCT FROM OLD.review_status AND NOT (
           (OLD.review_status = 'not_ready'  AND NEW.review_status = 'pending')
        OR (OLD.review_status = 'pending'    AND NEW.review_status IN ('not_ready', 'submitting'))
        OR (OLD.review_status = 'submitting' AND NEW.review_status IN ('pending', 'submitted'))
    ) THEN
        RAISE EXCEPTION 'segmentation batch % review cannot move from % to %',
            OLD.segmentation_batch_id, OLD.review_status, NEW.review_status
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.review_status = 'submitting' AND NEW.review_status = 'submitting'
       AND NEW.review_request_hash IS DISTINCT FROM OLD.review_request_hash THEN
        RAISE EXCEPTION
            'segmentation batch % is already submitting a different review',
            OLD.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.review_status = 'submitted' AND OLD.review_status <> 'submitted' THEN
        -- coalesce(..., false): `review_decision` is NULL for an undecided
        -- pair, and in three-valued logic an un-coalesced OR chain evaluates
        -- to NULL there — which would let exactly the case this check exists
        -- for (a ready pair nobody decided) through.
        SELECT count(*) INTO offending
        FROM segmentation_pairs p
        WHERE p.segmentation_batch_id = NEW.segmentation_batch_id
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
                'segmentation batch % cannot be submitted: % pair(s) lack a final '
                'decision consistent with their candidate and selected SEG',
                NEW.segmentation_batch_id, offending
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.domain_rows_enforce_producer()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    producer text;
BEGIN
    IF NEW.created_by_run_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT s.target_name INTO producer
    FROM node_runs r JOIN graph_nodes s ON s.node_id = r.node_id
    WHERE r.run_id = NEW.created_by_run_id;
    IF producer IS NULL THEN
        -- The FK reports the missing run with the right error.
        RETURN NEW;
    END IF;
    IF NOT (producer = ANY (TG_ARGV)) THEN
        RAISE EXCEPTION
            '% rows are owned by %; run % executes % (D17)',
            TG_TABLE_NAME, array_to_string(TG_ARGV, ' or '),
            NEW.created_by_run_id, producer
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.domain_rows_enforce_cohort_alignment()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    own_session uuid;
    cohort_session uuid;
BEGIN
    SELECT session_id INTO own_session
    FROM node_runs WHERE run_id = NEW.created_by_run_id;

    SELECT r.session_id INTO cohort_session
    FROM cohorts c JOIN node_runs r ON r.run_id = c.created_by_run_id
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
$function$;

CREATE OR REPLACE FUNCTION public.cohorts_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'cohort % is population provenance and cannot be deleted', OLD.cohort_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
       OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id
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
$function$;

CREATE OR REPLACE FUNCTION public.segmentation_pairs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
    FROM segmentation_batches WHERE segmentation_batch_id = NEW.segmentation_batch_id;
    IF run_review = 'submitted' THEN
        RAISE EXCEPTION
            'segmentation batch % review is submitted; its pairs are final',
            NEW.segmentation_batch_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;

    IF NEW.pair_id IS DISTINCT FROM OLD.pair_id
       OR NEW.segmentation_batch_id IS DISTINCT FROM OLD.segmentation_batch_id
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
    IF OLD.inference_job_id IS NOT NULL
       AND NEW.inference_job_id IS DISTINCT FROM OLD.inference_job_id THEN
        RAISE EXCEPTION
            'segmentation pair % already reconciles against MONAI job %',
            OLD.pair_id, OLD.inference_job_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.radiomics_features_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    pair record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'radiomics row % is research history and cannot be deleted', OLD.radiomics_feature_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'radiomics row % is immutable once written (A7); a different measurement '
            'is a different content-addressed row', OLD.radiomics_feature_id
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
          JOIN segmentation_batches sr ON sr.segmentation_batch_id = p.segmentation_batch_id
          JOIN node_runs r ON r.run_id = sr.created_by_run_id
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
$function$;

CREATE OR REPLACE FUNCTION public.feature_sets_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
       OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id
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
            FROM node_runs r WHERE r.run_id = NEW.created_by_run_id;
            SELECT count(*) - count(DISTINCT value) INTO offending
            FROM jsonb_array_elements_text(NEW.source_feature_ids) AS s(value);
            IF offending > 0 THEN
                RAISE EXCEPTION
                    'feature set % repeats % source radiomics row(s)', NEW.feature_set_id, offending
                    USING ERRCODE = 'check_violation';
            END IF;
            SELECT count(*) INTO offending
            FROM jsonb_array_elements_text(NEW.source_feature_ids) AS s(value)
            LEFT JOIN radiomics_features f ON f.radiomics_feature_id::text = s.value
            WHERE f.radiomics_feature_id IS NULL
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
$function$;

CREATE OR REPLACE FUNCTION public.model_results_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    set_frozen_at timestamptz;
    run_session uuid;
    file_session uuid;
    file_kind text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'model result % is research history and cannot be deleted', OLD.model_result_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.model_result_id IS DISTINCT FROM OLD.model_result_id
           OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id
           OR NEW.cohort_id IS DISTINCT FROM OLD.cohort_id
           OR NEW.feature_set_id IS DISTINCT FROM OLD.feature_set_id
           OR NEW.comparison_key IS DISTINCT FROM OLD.comparison_key
           OR NEW.variant_key IS DISTINCT FROM OLD.variant_key
           OR NEW.algorithm IS DISTINCT FROM OLD.algorithm
           OR NEW.feature_order IS DISTINCT FROM OLD.feature_order
           OR NEW.split_definition IS DISTINCT FROM OLD.split_definition
           OR NEW.seeds IS DISTINCT FROM OLD.seeds
           OR NEW.dataset_digest IS DISTINCT FROM OLD.dataset_digest
           OR NEW.input_file_id IS DISTINCT FROM OLD.input_file_id
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
    FROM node_runs WHERE run_id = NEW.created_by_run_id;

    IF NEW.predictions_file_id IS NOT NULL THEN
        SELECT session_id, kind INTO file_session, file_kind
        FROM stored_files WHERE file_id = NEW.predictions_file_id;
        IF FOUND AND (file_session IS DISTINCT FROM run_session
                      OR file_kind <> 'export_csv') THEN
            RAISE EXCEPTION
                'predictions file % must be an export_csv of session % (R1/R6)',
                NEW.predictions_file_id, run_session
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    IF NEW.input_file_id IS NOT NULL THEN
        SELECT session_id, kind INTO file_session, file_kind
        FROM stored_files WHERE file_id = NEW.input_file_id;
        IF FOUND AND (file_session IS DISTINCT FROM run_session
                      OR file_kind <> 'input_csv') THEN
            RAISE EXCEPTION
                'input file % must be an input_csv of session % (R1/R6)',
                NEW.input_file_id, run_session
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.reports_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
           OR NEW.created_by_run_id IS DISTINCT FROM OLD.created_by_run_id
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
    IF NEW.created_by_run_id IS NOT NULL THEN
        SELECT session_id INTO run_session
        FROM node_runs WHERE run_id = NEW.created_by_run_id;
        IF FOUND AND run_session IS DISTINCT FROM NEW.session_id THEN
            RAISE EXCEPTION
                'report % run % belongs to session %, not % (R1)',
                NEW.report_id, NEW.created_by_run_id,
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
$function$;

CREATE OR REPLACE FUNCTION public.report_images_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    report_session uuid;
    file_session uuid;
    file_kind text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'report image % cannot be deleted (reports are immutable history)',
            OLD.image_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    SELECT session_id INTO report_session
    FROM reports WHERE report_id = NEW.report_id;

    SELECT session_id, kind INTO file_session, file_kind
    FROM stored_files WHERE file_id = NEW.file_id;

    IF file_session IS DISTINCT FROM report_session THEN
        RAISE EXCEPTION
            'report_image % stored file % belongs to session %, not % (R1)',
            NEW.image_id, NEW.file_id, file_session, report_session
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF file_kind NOT IN ('figure_png', 'figure_jpeg') THEN
        RAISE EXCEPTION
            'report_image % stored file % has kind %; must be figure_png or figure_jpeg',
            NEW.image_id, NEW.file_id, file_kind
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$function$;
"""

# The same functions exactly as n1 left them, for downgrade().
_FUNCTIONS_N = r"""
CREATE OR REPLACE FUNCTION public.agent_runs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'agent run % is execution provenance and cannot be deleted',
            OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.agent_run_id IS DISTINCT FROM OLD.agent_run_id
       OR NEW.step_id IS DISTINCT FROM OLD.step_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id THEN
        RAISE EXCEPTION 'agent run % identity is immutable', OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'completed' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'agent run % is completed and immutable', OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.started_at IS NOT NULL AND (
           NEW.started_at IS DISTINCT FROM OLD.started_at
        OR NEW.compiled_prompt IS DISTINCT FROM OLD.compiled_prompt
        OR NEW.input_refs IS DISTINCT FROM OLD.input_refs) THEN
        RAISE EXCEPTION
            'agent run % compiled_prompt/input_refs/started_at are immutable once '
            'the run has started', OLD.agent_run_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'pending' AND NEW.status = 'running')
        OR (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
        OR (OLD.status = 'failed'  AND NEW.status = 'running')
    ) THEN
        RAISE EXCEPTION 'agent run % cannot move from % to %',
            OLD.agent_run_id, OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.plans_enforce_run_cardinality()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    target uuid;
    approved timestamptz;
    step_count integer;
    run_count integer;
    matched integer;
BEGIN
    target := COALESCE(NEW.session_id, OLD.session_id);
    SELECT approved_at INTO approved FROM plans WHERE session_id = target;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT count(*) INTO run_count FROM agent_runs WHERE session_id = target;

    IF approved IS NULL THEN
        IF run_count > 0 THEN
            RAISE EXCEPTION
                'draft plan for session % has % agent run(s); a draft has none '
                '(A21)', target, run_count
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NULL;
    END IF;

    SELECT count(*) INTO step_count FROM plan_steps WHERE session_id = target;
    SELECT count(*) INTO matched
    FROM plan_steps s JOIN agent_runs r ON r.step_id = s.step_id
    WHERE s.session_id = target;

    IF step_count = 0 OR run_count <> step_count OR matched <> step_count THEN
        RAISE EXCEPTION
            'approved plan for session % has % step(s) and % run(s) (% matched); '
            'approval requires exactly one run per step (A21)',
            target, step_count, run_count, matched
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.plan_steps_reject_approved_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    target uuid;
    approved timestamptz;
BEGIN
    target := COALESCE(NEW.session_id, OLD.session_id);
    SELECT approved_at INTO approved FROM plans WHERE session_id = target;
    IF approved IS NOT NULL THEN
        RAISE EXCEPTION
            'plan for session % was approved at %; its steps are immutable',
            target, approved
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;

CREATE OR REPLACE FUNCTION public.sessions_require_a_plan()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM plans WHERE session_id = NEW.session_id) THEN
        RAISE EXCEPTION
            'session % was committed with no plan; create the session and its '
            'empty plan in one transaction', NEW.session_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.plans_reject_orphan_delete()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF EXISTS (SELECT 1 FROM sessions WHERE session_id = OLD.session_id) THEN
        RAISE EXCEPTION
            'plan % cannot be deleted while its session exists', OLD.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$function$;

CREATE OR REPLACE FUNCTION public.plans_reject_unapproval()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.approved_at IS NOT NULL
       AND NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
        RAISE EXCEPTION
            'plan approval is one-way (session %)', OLD.session_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.artifacts_enforce_immutable_bytes()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.artifact_id IS DISTINCT FROM OLD.artifact_id
       OR NEW.session_id IS DISTINCT FROM OLD.session_id
       OR NEW.logical_key IS DISTINCT FROM OLD.logical_key
       OR NEW.retention_class IS DISTINCT FROM OLD.retention_class THEN
        RAISE EXCEPTION
            'artifact % identity is immutable', OLD.artifact_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.retention_class = 'user_retained' AND (
           NEW.sha256 IS DISTINCT FROM OLD.sha256
        OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
        OR NEW.body IS DISTINCT FROM OLD.body
    ) THEN
        RAISE EXCEPTION
            'artifact % holds retained bytes; they are immutable. Publish a new '
            'logical_key instead of replacing them', OLD.artifact_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- A13: a system_render row catalogues a REGENERABLE render. Its digest is
    -- the last-seen content, not a promise -- editing a mask legitimately
    -- produces a new GIF under the same key. The render PATH is still fixed,
    -- so the row cannot be re-pointed at some other file.
    IF OLD.retention_class = 'system_render'
       AND NEW.render_path IS DISTINCT FROM OLD.render_path THEN
        RAISE EXCEPTION
            'artifact % render_path is immutable; catalogue a new key for a '
            'different render', OLD.artifact_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.segmentation_runs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.domain_rows_enforce_producer()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.domain_rows_enforce_cohort_alignment()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.cohorts_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.segmentation_pairs_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.radiomics_features_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.feature_sets_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.model_results_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.reports_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.report_images_enforce_row_rules()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$;
"""

_RETENTION_CHECKS = {
    "retention_class": "retention_class IN ({retained}, {render})",
    "retention_shape": (
        "(retention_class = {render} AND body IS NULL AND render_path IS NOT NULL)"
        " OR "
        "(retention_class = {retained} AND body IS NOT NULL AND render_path IS NULL)"
    ),
}


def _create_retention_checks(table: str, retained: str, render: str) -> None:
    for name, rule in _RETENTION_CHECKS.items():
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_{name} CHECK ("
            + rule.format(retained=f"'{retained}'", render=f"'{render}'")
            + ")"
        )


def _drop_retention_checks(table: str) -> None:
    for name in _RETENTION_CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_{name}")


def _rewrite_retention(table: str, trigger: str, mapping: dict[str, str]) -> None:
    # The bytes trigger keeps retention_class immutable; this is the one
    # sanctioned rewrite of the value itself.
    op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    for old, new in mapping.items():
        op.execute(
            f"UPDATE {table} SET retention_class = '{new}' WHERE retention_class = '{old}'"
        )
    op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")


def _rewrite_config_key(table: str, trigger: str, old: str, new: str) -> None:
    # The row-rules trigger keeps config immutable; this is the one sanctioned
    # rewrite of a key inside it.
    op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    op.execute(
        f"UPDATE {table} SET config = (config - '{old}') "
        f"|| jsonb_build_object('{new}', config -> '{old}') "
        f"WHERE config ? '{old}'"
    )
    op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")


def upgrade() -> None:
    _drop_retention_checks("artifacts")

    for old, new in TABLE_RENAMES:
        op.execute(f"ALTER TABLE {old} RENAME TO {new}")
    for table, old, new in COLUMN_RENAMES:
        op.execute(f'ALTER TABLE {table} RENAME COLUMN "{old}" TO "{new}"')
    for table, old, new in CONSTRAINT_RENAMES:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")
    for old, new in INDEX_RENAMES:
        op.execute(f"ALTER INDEX {old} RENAME TO {new}")
    for old, new in SEQUENCE_RENAMES:
        op.execute(f"ALTER SEQUENCE {old} RENAME TO {new}")

    for old, new in FUNCTION_RENAMES:
        op.execute(f"ALTER FUNCTION {old}() RENAME TO {new}")
    op.execute(_FUNCTIONS_O)
    for table, old, new, is_constraint in TRIGGER_RENAMES:
        op.execute(f"ALTER TRIGGER {old} ON {table} RENAME TO {new}")
        if is_constraint:
            op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")

    _rewrite_config_key(
        "segmentation_batches",
        "segmentation_batches_row_rules",
        "correlation_run_id",
        "batch_correlation_id",
    )
    _rewrite_retention(
        "stored_files",
        "stored_files_bytes_are_immutable",
        {"user_retained": "retained", "system_render": "render"},
    )
    _create_retention_checks("stored_files", "retained", "render")


def downgrade() -> None:
    _drop_retention_checks("stored_files")
    _rewrite_retention(
        "stored_files",
        "stored_files_bytes_are_immutable",
        {"retained": "user_retained", "render": "system_render"},
    )
    _rewrite_config_key(
        "segmentation_batches",
        "segmentation_batches_row_rules",
        "batch_correlation_id",
        "correlation_run_id",
    )

    for table, old, new, is_constraint in reversed(TRIGGER_RENAMES):
        op.execute(f"ALTER TRIGGER {new} ON {table} RENAME TO {old}")
        if is_constraint:
            op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}")
    for old, new in reversed(FUNCTION_RENAMES):
        op.execute(f"ALTER FUNCTION {new}() RENAME TO {old}")
    op.execute(_FUNCTIONS_N)

    for old, new in reversed(SEQUENCE_RENAMES):
        op.execute(f"ALTER SEQUENCE {new} RENAME TO {old}")
    for old, new in reversed(INDEX_RENAMES):
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    for table, old, new in reversed(CONSTRAINT_RENAMES):
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}")
    for table, old, new in reversed(COLUMN_RENAMES):
        op.execute(f'ALTER TABLE {table} RENAME COLUMN "{new}" TO "{old}"')
    for old, new in reversed(TABLE_RENAMES):
        op.execute(f"ALTER TABLE {new} RENAME TO {old}")

    _create_retention_checks("artifacts", "user_retained", "system_render")
