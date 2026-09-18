import logging
import aiohttp
from application_api.core.config import settings

logger = logging.getLogger(__name__)

ORTHANC_URL = settings.ORTHANC_URL.rstrip("/")
ORTHANC_AUTH = aiohttp.BasicAuth(settings.ORTHANC_USER, settings.ORTHANC_PASSWORD)
ORTHANC_TIMEOUT = aiohttp.ClientTimeout(total=settings.ORTHANC_REQUEST_TIMEOUT)
ORTHANC_DELETE_SUCCESS_STATUSES = {200, 204}


def create_orthanc_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(auth=ORTHANC_AUTH, timeout=ORTHANC_TIMEOUT)


async def delete_orthanc_resource(
    collection: str,
    resource_id: str | None,
) -> tuple[bool, str | None]:
    if not resource_id:
        # A row with no stored Orthanc id has nothing to clean up in PACS —
        # but this is worth a log line rather than a silent no-op, since it
        # can also mean the id was simply never recorded (see D-3 in
        # database_audit.md) and a PACS copy could still exist unlinked.
        logger.warning(
            "delete_orthanc_resource(%s) called with no resource id; nothing to delete in Orthanc.",
            collection,
        )
        return True, None

    async with create_orthanc_session() as session:
        try:
            async with session.delete(f"{ORTHANC_URL}/{collection}/{resource_id}") as resp:
                if resp.status == 404:
                    return True, None

                if resp.status not in ORTHANC_DELETE_SUCCESS_STATUSES:
                    error_msg = f"Failed to delete from Orthanc: HTTP {resp.status}"
                    logger.error(error_msg)
                    return False, error_msg

                return True, None
        except aiohttp.ClientError as exc:
            error_msg = f"Orthanc connection error: {exc}"
            logger.error(error_msg)
            return False, error_msg


async def cleanup_orthanc_resources_after_db_delete(
    collection: str,
    resource_ids: list[str | None],
    *,
    context: str,
) -> list[str]:
    """Best-effort Orthanc cleanup to run AFTER the DB delete has committed.

    Deletes are now DB-first: once the database row (and its cascades) are
    gone, the database is no longer the source of truth for whether that
    imaging should exist in PACS, so a failure here cannot be reported back
    to the caller as "delete failed" — the delete already happened. Instead
    every id that failed to delete is logged clearly as an orphaned Orthanc
    resource, since that is the only place this information can still live.

    Returns the list of resource_ids that could NOT be deleted from Orthanc.
    """
    orphaned: list[str] = []
    for resource_id in resource_ids:
        if not resource_id:
            continue
        ok, error_msg = await delete_orthanc_resource(collection, resource_id)
        if not ok:
            orphaned.append(resource_id)
            logger.error(
                "Orphaned Orthanc %s %s after %s: %s",
                collection, resource_id, context, error_msg,
            )
    if orphaned:
        logger.warning(
            "%s: %d Orthanc %s resource(s) left orphaned: %s",
            context, len(orphaned), collection, orphaned,
        )
    return orphaned
