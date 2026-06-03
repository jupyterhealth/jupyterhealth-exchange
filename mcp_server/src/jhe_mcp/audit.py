"""Deliberate, structured audit log for JHE data access.

Records WHO (subject), WHAT (method + resource path — the path intentionally
includes the target study/patient id for audit traceability), and RESULT
(HTTP status). Never logs response bodies or token values, so it carries
identifiers but no PHI.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("jhe_mcp.audit")


def log_access(*, subject: str | None, method: str, path: str, status: int) -> None:
    """Emit a single structured audit line for one JHE data access."""
    logger.info(
        "audit subject=%s method=%s path=%s status=%s",
        subject,
        method,
        path,
        status,
        extra={
            "audit": True,
            "subject": subject,
            "method": method,
            "path": path,
            "status": status,
        },
    )
