"""Naming helpers for segmentation artifacts."""

from datetime import datetime


def build_segmentation_series_description(
    model_name: str,
    label: str,
    created_at: datetime | None = None,
) -> str:
    """Match the assisted-segmentation mask naming convention."""
    timestamp = created_at or datetime.now()
    return f"{timestamp:%d/%m/%Y} - {model_name.strip()} - {label.strip()}"
