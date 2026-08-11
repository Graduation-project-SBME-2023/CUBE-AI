"""Uvicorn logging helpers for service-specific startup messages."""

from copy import deepcopy

from uvicorn.config import LOGGING_CONFIG


class _StartupMessageFilter:
    def __init__(self, message: str):
        self.message = message

    def filter(self, record):
        if record.getMessage() == "Application startup complete.":
            record.msg = self.message
            record.args = ()
        return True


def uvicorn_log_config(service_name: str):
    """Return Uvicorn's standard config with an accurate startup message."""
    config = deepcopy(LOGGING_CONFIG)
    config["filters"] = {
        "service_startup": {
            "()": _StartupMessageFilter,
            "message": f"{service_name} worker startup complete.",
        }
    }
    config["handlers"]["default"]["filters"] = ["service_startup"]
    return config
