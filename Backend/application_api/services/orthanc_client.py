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
