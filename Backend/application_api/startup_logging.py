"""Uvicorn logging helpers for service-specific startup messages."""

import os
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

    # Uvicorn's config names only its own loggers and disables every other one,
    # so `logging.getLogger(__name__)` in this service resolves to a logger with
    # no handler -- every log line the application writes is silently dropped and
    # the container shows nothing but the access log. Give the application's own
    # loggers the same handler uvicorn uses.
    config["disable_existing_loggers"] = False
    config["loggers"]["application_api"] = {
        "handlers": ["default"],
        "level": os.environ.get("LOG_LEVEL", "INFO").upper(),
        "propagate": False,
    }
    return config
