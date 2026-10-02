"""Result shapes of the DB read tools.

Phase 6.1 renamed the Application API's `model_id` to `inference_model_id`.
These tools return API rows as their result, and a tool's success result shape
does not change (plan.md, global rules), so the rows keep the `model_id` key.
"""

from typing import Any


def keep_model_id(data: Any) -> Any:
    if isinstance(data, list):
        return [keep_model_id(item) for item in data]
    if isinstance(data, dict) and "inference_model_id" in data:
        data = dict(data)
        data["model_id"] = data.pop("inference_model_id")
    return data
