def raise_for_segmentation_error(result, volume_uid: str, label: str) -> None:
    if isinstance(result, dict) and (result.get("error") or result.get("upload_error")):
        stage = result.get("stage") or "upload"
        message = result.get("message") or result.get("upload_response") or "Unknown segmentation error"
        raise RuntimeError(
            f"Segmentation failed for volume {volume_uid}, label {label}, stage {stage}: {message}"
        )
