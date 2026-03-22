import logging

logger = logging.getLogger(__name__)
async def run_automatic_pipeline(
    volumes,
    labels,
    selected_auto_model,
    segmentation_service
):
        logger.info(f"Starting automatic segmentation on {len(volumes)} volumes using model '{selected_auto_model}'")

        for idx, volume_uid in enumerate(volumes, start=1):
            logger.info(f"[{idx}/{len(volumes)}] Running segmentation for volume: {volume_uid}")
            try:
                result = await segmentation_service.run_test_segmentation(
                    model=selected_auto_model,
                    series_instance_uid=volume_uid,
                    label_info=labels
                )
                logger.info(f"Completed segmentation for volume: {volume_uid}")
            except Exception as e:
                logger.error(f"Segmentation failed for {volume_uid} : {e}")    
