"""
logging_config.py — Shared logger configuration for all CRUD Lambda handlers.

CONTRACT (shared with CloudWatch Metric Filter):
  Every error log record MUST start with the literal prefix "ERROR:" with no
  preceding characters.  The Metric Filter pattern is `ERROR:` and relies on
  this exact format to count errors.  Do NOT change the format string here
  without simultaneously updating the Metric Filter.

Usage in a handler or repository module:
    from common.logging_config import configure_logger
    logger = configure_logger(__name__)   # module-level, called once
    ...
    logger.error("ERROR: something failed", exc_info=True)
"""

import logging

# Log format that guarantees "ERROR: <message>" at the start of the record
# when logger.error() is called.  The levelname ("ERROR") is included by
# CloudWatch automatically via the Lambda runtime, but we also embed it in the
# message prefix so that the Metric Filter pattern `ERROR:` matches reliably
# regardless of how the formatter is configured downstream.
_LOG_FORMAT = "%(levelname)s: %(message)s"


def configure_logger(name: str) -> logging.Logger:
    """Configure and return a Logger whose error records start with 'ERROR:'.

    The returned logger:
      - Uses the standard ``logging`` module (never ``print``).
      - Emits exactly one log record per exception when called with
        ``exc_info=True`` — the caller is responsible for not calling the
        logger multiple times for the same exception.
      - Has a StreamHandler to stdout so CloudWatch Logs receives the output.
      - Format: ``ERROR: <message>\\n<traceback>`` (for error-level records).

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A configured ``logging.Logger`` instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if configure_logger is called more than
    # once (e.g., during warm invocations or in tests).
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)

    # Lambda sets the root logger level; we set WARNING here so that debug
    # noise is suppressed by default, but ERROR records always propagate.
    logger.setLevel(logging.WARNING)

    return logger
