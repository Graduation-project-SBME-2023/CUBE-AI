import logging
from agent_system.models.tool_logging import ProgressPayload, TextPayload, ToolEvent
from agent_system.tool_logging import receive_tool_event

logger = logging.getLogger(__name__)
async def run_automatic_pipeline(
    volumes,
    labels,
    selected_auto_models,
    segmentation_service,
    session_id
):
        # ================ logging ======================
        payload = TextPayload(message=f'Starting Automatic Segmentation on {len(volumes)}...')
        tool_started = ToolEvent(
            event_type='tool_started',
            source='segmentation_agent',
            content_type='text',
            content=payload,
            session_id=session_id,
        )
        await receive_tool_event(tool_started)
        tool_progress = ToolEvent(
            event_type='tool_progress',
            source='segmentation_agent',
            content_type='progress',
            content=payload,
            session_id=session_id,
        )
        await receive_tool_event(tool_progress)
        #===============================================
        logger.info(f"Starting automatic segmentation on {len(volumes)} volumes")

        for idx, volume_uid in enumerate(volumes, start=1):
            logger.info(f"[{idx}/{len(volumes)}] Running segmentation for volume: {volume_uid}")
            # ============ logging =================
            payload = ProgressPayload(
                current=idx,
                total=len(volumes),
                label='Segmenting Volumes'
            )
            tool_progress = ToolEvent(
                event_type='tool_progress',
                source='segmentation_agent',
                content_type='progress',
                content=payload,
                session_id=session_id,
            )
            await receive_tool_event(tool_progress)
            # =======================================
            try:
                for i, label in enumerate(labels):     
                    result = await segmentation_service.run_segmentation(
                        model=selected_auto_models[i] if selected_auto_models and i < len(selected_auto_models) else None,
                        series_instance_uid=volume_uid,
                        label_info=label
                    )
                logger.info(f"Completed segmentation for volume: {volume_uid}")
            except Exception as e:
                logger.error(f"Segmentation failed for {volume_uid} : {e}")    
