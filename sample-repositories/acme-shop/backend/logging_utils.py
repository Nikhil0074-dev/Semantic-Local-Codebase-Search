import logging
import traceback

logger = logging.getLogger("acme.api")


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def log_api_error(request_path: str, status: int, exc: Exception) -> None:
    """Write a structured record of a failed API call, including the stack trace."""
    logger.error("api_error path=%s status=%s type=%s detail=%s\n%s", request_path, status,
                 type(exc).__name__, exc, "".join(traceback.format_exception(exc)))
