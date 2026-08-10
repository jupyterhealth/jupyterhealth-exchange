"""Deliberate, structured audit log for JHE data access.

Records WHO (subject), WHAT (method + resource path), and RESULT (HTTP
status). Where the target id is part of the path (reads, per-patient queries)
it is intentionally included for audit traceability; search requests carry
their criteria (names, birthdates) only in query params, which are NEVER
logged — a search audit line records that a search happened, not against
whom. Never logs response bodies or token values, so it carries identifiers
but no PHI.
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
