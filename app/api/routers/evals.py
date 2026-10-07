"""Evaluation runs history endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.core.errors import NotFoundError
from app.models.eval import EvalRun

router = APIRouter(tags=["evals"])


class EvalRunOut(BaseModel):
    id: int
    suite: str
    mode: str = "offline"
    metrics: dict = {}
    config: dict = {}
    report_path: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/evals/runs", response_model=list[EvalRunOut], summary="List evaluation runs")
def list_eval_runs(
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[EvalRunOut]:
    stmt = select(EvalRun).order_by(EvalRun.id.desc()).limit(page.limit).offset(page.offset)
    return [EvalRunOut.model_validate(r) for r in db.execute(stmt).scalars()]


@router.get("/evals/runs/{id}", response_model=EvalRunOut, summary="Get one evaluation run")
def get_eval_run(
    id: int,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> EvalRunOut:
    run = db.get(EvalRun, id)
    if run is None:
        raise NotFoundError("evaluation run not found")
    return EvalRunOut.model_validate(run)
