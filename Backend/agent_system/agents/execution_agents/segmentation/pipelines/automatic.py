import logging

logger = logging.getLogger(__name__)
async def run_automatic_pipeline(
    volumes,
    labels,
    selected_auto_models,
    segmentation_service
):
        logger.info(f"Starting automatic segmentation on {len(volumes)} volumes")

        for idx, volume_uid in enumerate(volumes, start=1):
            logger.info(f"[{idx}/{len(volumes)}] Running segmentation for volume: {volume_uid}")
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
