from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import POStatus, PurchaseOrder

logger = logging.getLogger("backend.connectors.po_system")


@dataclass(frozen=True)
class POUpdateResult:
    ok: bool
    po_number: str
    old_version: int | None = None
    new_version: int | None = None
    status: str | None = None
    error: str | None = None


def update_po(
    db: Session,
    po_number: str,
    new_status: POStatus,
    notes: str,
    expected_version: int | None = None,
) -> POUpdateResult:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first()

    if not po:
        logger.warning("PO %s not found", po_number)
        return POUpdateResult(ok=False, po_number=po_number, error=f"PO {po_number} not found")

    if expected_version is not None and po.version != expected_version:
        logger.warning(
            "Optimistic concurrency conflict on %s: expected v%d, found v%d",
            po_number, expected_version, po.version,
        )
        return POUpdateResult(
            ok=False,
            po_number=po_number,
            old_version=po.version,
            error=f"Version conflict: expected {expected_version}, found {po.version}",
        )

    old_version = po.version
    new_version = old_version + 1

    rows = db.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == po.id, PurchaseOrder.version == old_version)
        .values(
            status=new_status,
            notes=f"{po.notes}\n{notes}".strip() if po.notes else notes,
            version=new_version,
            updated_at=datetime.now(timezone.utc),
        )
    ).rowcount

    if rows == 0:
        logger.warning("Concurrent update on %s — row-level conflict", po_number)
        return POUpdateResult(
            ok=False,
            po_number=po_number,
            old_version=old_version,
            error="Concurrent modification detected",
        )

    db.flush()
    logger.info("PO %s updated: v%d -> v%d, status=%s", po_number, old_version, new_version, new_status.value)
    return POUpdateResult(
        ok=True,
        po_number=po_number,
        old_version=old_version,
        new_version=new_version,
        status=new_status.value,
    )
