"""Ladowanie/zapis danych formularza: rozdziela sekcje generyczne (form_instances.data)
od sekcji materializowanych w dedykowanych tabelach (patrz `app/models`).

Kazda sekcja schematu formularza (`app/forms/definitions/*.json`) ma `data_source`:
- "form_instance" (domyslnie) -> przechowywana wprost w `form_instances.data[key]`.
- nazwa tabeli strukturalnej z `REPLACE_MODELS` -> przy zapisie stary zestaw wierszy
  projektu jest w calosci zastepowany nowym (`mode: "replace"`, domyslny dla tych tabel).
- "evm_snapshots" / "milestone_forecasts" (`mode: "append"`) -> kazdy zapis dopisuje
  nowy wiersz historii (do wykresow S-krzywej EVM / trendu MTA), nie nadpisuje.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..calculators.cpm import format_predecessors, parse_predecessors
from ..models.business_case import BusinessCaseVariant
from ..models.dpci import DpciScore
from ..models.evm import EvmSnapshot
from ..models.form_instance import FormInstance
from ..models.milestone import Milestone, MilestoneForecast
from ..models.risk import Risk
from ..models.schedule import ScheduleTask, WbsItem

REPLACE_MODELS: dict[str, type] = {
    "wbs_items": WbsItem,
    "dpci_scores": DpciScore,
    "risks": Risk,
    "schedule_tasks": ScheduleTask,
    "business_case_variants": BusinessCaseVariant,
    "milestones": Milestone,
}

_MODEL_COLUMNS: dict[type, set[str]] = {
    model: {c.name for c in model.__table__.columns if c.name not in ("id", "project_id")}
    for model in REPLACE_MODELS.values()
}


def _filter_row(model: type, row: dict[str, Any]) -> dict[str, Any]:
    allowed = _MODEL_COLUMNS[model]
    out = {k: v for k, v in row.items() if k in allowed and v not in ("", None)}
    if model is BusinessCaseVariant and isinstance(out.get("cashflows"), str):
        out["cashflows"] = [
            float(x) for x in out["cashflows"].replace(";", ",").split(",") if x.strip()
        ]
    if model is ScheduleTask and isinstance(out.get("predecessors"), str):
        # UI edytuje poprzednikow jako DSL tekstowy "KOD:TYP:LAG;..." (formularz 09);
        # w bazie trzymamy ustrukturyzowana liste [{"code","type","lag"}, ...].
        out["predecessors"] = [
            {"code": p.code, "type": p.type, "lag": p.lag} for p in parse_predecessors(out["predecessors"])
        ]
    return out


def _get_or_create_instance(db: Session, project_id: int, slug: str) -> FormInstance:
    stmt = select(FormInstance).where(
        FormInstance.project_id == project_id,
        FormInstance.form_slug == slug,
        FormInstance.version == 1,
    )
    instance = db.execute(stmt).scalar_one_or_none()
    if instance is None:
        instance = FormInstance(project_id=project_id, form_slug=slug, version=1, data={})
        db.add(instance)
    return instance


def load_form_sections(db: Session, project_id: int, form_def: dict) -> dict[str, Any]:
    instance = db.execute(
        select(FormInstance).where(
            FormInstance.project_id == project_id,
            FormInstance.form_slug == form_def["slug"],
            FormInstance.version == 1,
        )
    ).scalar_one_or_none()
    blob = instance.data if instance else {}

    sections: dict[str, Any] = {}
    for section in form_def["sections"]:
        key = section["key"]
        data_source = section.get("data_source", "form_instance")

        if section["type"] not in ("table", "fields") or data_source == "form_instance":
            sections[key] = blob.get(key)
            continue

        if data_source in REPLACE_MODELS:
            model = REPLACE_MODELS[data_source]
            rows = db.execute(select(model).where(model.project_id == project_id)).scalars().all()
            out_rows = [{c: getattr(r, c) for c in _MODEL_COLUMNS[model]} for r in rows]
            if model is ScheduleTask:
                for r in out_rows:
                    r["predecessors"] = format_predecessors(r.get("predecessors") or [])
            sections[key] = out_rows
        elif data_source == "evm_snapshots":
            rows = (
                db.execute(
                    select(EvmSnapshot)
                    .where(EvmSnapshot.project_id == project_id)
                    .order_by(EvmSnapshot.report_date)
                )
                .scalars()
                .all()
            )
            sections[key] = {
                "history": [
                    {"report_date": r.report_date, "bac": r.bac, "pv": r.pv, "ev": r.ev, "ac": r.ac, "note": r.note}
                    for r in rows
                ],
                "latest": None,
            }
        elif data_source == "milestone_forecasts":
            rows = (
                db.execute(
                    select(MilestoneForecast, Milestone.name)
                    .join(Milestone, Milestone.id == MilestoneForecast.milestone_id)
                    .where(Milestone.project_id == project_id)
                    .order_by(MilestoneForecast.report_date)
                )
                .all()
            )
            sections[key] = [
                {
                    "milestone_name": name,
                    "report_date": fc.report_date,
                    "forecast_date": fc.forecast_date,
                }
                for fc, name in rows
            ]
        else:
            sections[key] = blob.get(key)

    return sections


def save_form_sections(
    db: Session, project_id: int, form_def: dict, sections: dict[str, Any], status: str
) -> None:
    instance = _get_or_create_instance(db, project_id, form_def["slug"])
    instance.status = status
    blob = dict(instance.data or {})

    for section in form_def["sections"]:
        key = section["key"]
        if key not in sections:
            continue
        value = sections[key]
        data_source = section.get("data_source", "form_instance")
        mode = section.get("mode", "replace")

        if section["type"] not in ("table", "fields") or data_source == "form_instance":
            blob[key] = value
            continue

        if data_source in REPLACE_MODELS and mode == "replace":
            model = REPLACE_MODELS[data_source]
            db.execute(delete(model).where(model.project_id == project_id))
            for row in value or []:
                db.add(model(project_id=project_id, **_filter_row(model, row)))
        elif data_source == "evm_snapshots" and mode == "append":
            if value:
                db.add(
                    EvmSnapshot(
                        project_id=project_id,
                        report_date=value["report_date"],
                        bac=value["bac"],
                        pv=value["pv"],
                        ev=value["ev"],
                        ac=value["ac"],
                        note=value.get("note"),
                    )
                )
        elif data_source == "milestone_forecasts" and mode == "append":
            for row in value or []:
                milestone = db.execute(
                    select(Milestone).where(
                        Milestone.project_id == project_id, Milestone.name == row["milestone_name"]
                    )
                ).scalar_one_or_none()
                if milestone is None:
                    milestone = Milestone(project_id=project_id, name=row["milestone_name"])
                    db.add(milestone)
                    db.flush()
                db.add(
                    MilestoneForecast(
                        milestone_id=milestone.id,
                        report_date=row["report_date"],
                        forecast_date=row["forecast_date"],
                    )
                )
        else:
            blob[key] = value

    instance.data = blob
    db.commit()
