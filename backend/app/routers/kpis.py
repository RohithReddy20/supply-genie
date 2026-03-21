from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import KPIDashboard
from app.services.kpi import compute_kpis

router = APIRouter(prefix="/kpis", tags=["kpis"])


@router.get("", response_model=KPIDashboard)
async def get_kpis(db: Session = Depends(get_db)) -> KPIDashboard:
    data = compute_kpis(db)
    return KPIDashboard(**data)
