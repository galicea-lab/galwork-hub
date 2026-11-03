from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calculators import dpci as dpci_calc
from ..calculators import emv_ryzyko
from ..calculators import evm as evm_calc
from ..calculators import roi_npv as roi_npv_calc
from ..calculators.cpm import CpmError, Predecessor, Task, compute_cpm
from ..calculators.mta import ForecastPoint, analyze_milestone_trend
from api.dependencies import get_db
from ..models.business_case import BusinessCaseVariant
from ..models.dpci import DpciScore
from ..models.evm import EvmSnapshot
from ..models.milestone import Milestone, MilestoneForecast
from ..models.risk import Risk
from ..models.schedule import ScheduleTask
from ..forms.registry import FormNotFoundError, get_form
from ..services.form_data import load_form_sections
from ..services.report_export import render_form_html

router = APIRouter(prefix="/api/projects", tags=["dashboard"])


@router.get("/{project_id}/dashboard")
def get_dashboard(project_id: int, db: Session = Depends(get_db)):
    evm_history = (
        db.execute(
            select(EvmSnapshot)
            .where(EvmSnapshot.project_id == project_id)
            .order_by(EvmSnapshot.report_date)
        )
        .scalars()
        .all()
    )
    evm_series = [
        {
            "report_date": s.report_date,
            **evm_calc.EVMData(BAC=s.bac, PV=s.pv, EV=s.ev, AC=s.ac).compute(),
        }
        for s in evm_history
    ]

    risks = db.execute(select(Risk).where(Risk.project_id == project_id)).scalars().all()
    risk_summary = emv_ryzyko.rezerwa(
        [emv_ryzyko.Ryzyko(r.description, r.probability, r.impact) for r in risks]
    )

    dpci_rows = db.execute(select(DpciScore).where(DpciScore.project_id == project_id)).scalars().all()
    dpci_result = None
    if dpci_rows:
        scores: dict[str, list[int]] = {}
        for r in dpci_rows:
            scores.setdefault(r.category, []).append(r.score)
        try:
            dpci_result = dpci_calc.compute_dpci(scores)
        except (ValueError, KeyError):
            dpci_result = None

    variants = (
        db.execute(select(BusinessCaseVariant).where(BusinessCaseVariant.project_id == project_id))
        .scalars()
        .all()
    )
    business_case = [
        {
            "variant_name": v.variant_name,
            "discount_rate": v.discount_rate,
            **roi_npv_calc.compute_variant(v.cashflows, v.discount_rate),
        }
        for v in variants
    ]

    milestones = (
        db.execute(select(Milestone).where(Milestone.project_id == project_id)).scalars().all()
    )
    milestone_trends = []
    for m in milestones:
        forecasts = (
            db.execute(
                select(MilestoneForecast)
                .where(MilestoneForecast.milestone_id == m.id)
                .order_by(MilestoneForecast.report_date)
            )
            .scalars()
            .all()
        )
        trend = analyze_milestone_trend(
            m.baseline_date,
            [ForecastPoint(f.report_date, f.forecast_date) for f in forecasts],
        )
        milestone_trends.append({"milestone": m.name, **jsonable_encoder(trend)})

    schedule_rows = (
        db.execute(select(ScheduleTask).where(ScheduleTask.project_id == project_id))
        .scalars()
        .all()
    )
    cpm_result = None
    if schedule_rows and all(t.duration is not None or t.most_likely is not None for t in schedule_rows):
        try:
            tasks = [
                Task(
                    code=t.code,
                    name=t.name,
                    duration=t.duration if t.duration is not None else t.most_likely,
                    predecessors=[Predecessor(**p) for p in (t.predecessors or [])],
                )
                for t in schedule_rows
            ]
            cpm_result = jsonable_encoder(compute_cpm(tasks))
        except CpmError:
            cpm_result = None

    return {
        "evm_series": jsonable_encoder(evm_series),
        "risk_summary": risk_summary,
        "dpci": dpci_result,
        "business_case": business_case,
        "milestone_trends": milestone_trends,
        "cpm": cpm_result,
    }


@router.get("/{project_id}/reports/{slug}/export", response_class=HTMLResponse)
def export_form_report(project_id: int, slug: str, db: Session = Depends(get_db)):
    try:
        form_def = get_form(slug)
    except FormNotFoundError:
        return HTMLResponse(f"<p>Formularz '{slug}' nie znaleziony</p>", status_code=404)

    sections = load_form_sections(db, project_id, form_def)
    html = render_form_html(form_def, sections)
    return HTMLResponse(html)
