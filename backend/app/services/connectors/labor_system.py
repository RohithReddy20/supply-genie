from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("backend.connectors.labor_system")


@dataclass(frozen=True)
class LaborUpdateResult:
    ok: bool
    site_id: str
    worker_name: str
    shift_date: str
    status: str = "absent"
    coverage_needed: bool = True
    error: str | None = None


def update_labor_record(
    site_id: str,
    worker_name: str,
    shift_date: str,
    role: str,
    reason: str = "",
    status: str = "absent",
) -> LaborUpdateResult:
    """Mark a worker as absent in the labor planning system and flag the shift
    for replacement coverage.

    This is a mock connector — in production it would call an HRIS / WFM API
    (e.g. Kronos, ADP, SAP SuccessFactors).
    """
    try:
        # Simulate validation
        if not site_id or not worker_name:
            return LaborUpdateResult(
                ok=False,
                site_id=site_id,
                worker_name=worker_name,
                shift_date=shift_date,
                error="site_id and worker_name are required",
            )

        logger.info(
            "Labor system updated: %s at %s marked '%s' for shift %s (role: %s, reason: %s)",
            worker_name, site_id, status, shift_date, role, reason,
        )

        return LaborUpdateResult(
            ok=True,
            site_id=site_id,
            worker_name=worker_name,
            shift_date=shift_date,
            status=status,
            coverage_needed=True,
        )
    except Exception as exc:
        error_msg = str(exc)
        logger.error("Labor system error: %s", error_msg)
        return LaborUpdateResult(
            ok=False,
            site_id=site_id,
            worker_name=worker_name,
            shift_date=shift_date,
            error=error_msg,
        )
