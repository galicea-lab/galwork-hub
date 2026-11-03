from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from ..calculators import ccpm, cpm, dpci, emv_ryzyko, evm, pert, roi_npv
from ..calculators.mta import ForecastPoint, analyze_milestone_trend
from ..schemas.calc import (
    CcpmBufferRequest,
    CcpmFeverRequest,
    CpmFromTasksRequest,
    CpmRequest,
    DpciRequest,
    EmvRequest,
    EvmRequest,
    MtaRequest,
    PertRequest,
    RoiNpvRequest,
)

router = APIRouter(prefix="/api/calc", tags=["calc"])


@router.post("/roi-npv")
def calc_roi_npv(payload: RoiNpvRequest):
    return roi_npv.compute_variant(payload.flows, payload.rate)


@router.post("/dpci")
def calc_dpci(payload: DpciRequest):
    return dpci.compute_dpci(payload.scores)


@router.post("/emv")
def calc_emv(payload: EmvRequest):
    ryzyka = [emv_ryzyko.Ryzyko(r.nazwa, r.prawd, r.wplyw) for r in payload.risks]
    return emv_ryzyko.rezerwa(ryzyka)


@router.post("/pert")
def calc_pert(payload: PertRequest):
    zadania = [pert.Zadanie(t.nazwa, t.O, t.M, t.P) for t in payload.tasks]
    return pert.compute_pert(zadania, payload.t_s)


@router.post("/evm")
def calc_evm(payload: EvmRequest):
    return evm.EVMData(BAC=payload.bac, PV=payload.pv, EV=payload.ev, AC=payload.ac).compute()


@router.post("/cpm")
def calc_cpm(payload: CpmRequest):
    tasks = [
        cpm.Task(
            code=t.code,
            name=t.name,
            duration=t.duration,
            predecessors=[cpm.Predecessor(p.code, p.type, p.lag) for p in t.predecessors],
        )
        for t in payload.tasks
    ]
    result = cpm.compute_cpm(tasks)
    return jsonable_encoder(result)


@router.post("/cpm-from-tasks")
def calc_cpm_from_tasks(payload: CpmFromTasksRequest):
    """Jak /cpm, ale przyjmuje surowe wiersze tabeli `schedule_tasks` (formularz 09) —
    parsuje DSL poprzednikow i spada z `duration` na `most_likely`, gdy brak deterministycznego czasu."""
    tasks = [
        cpm.task_from_raw(
            code=t.code,
            name=t.name,
            duration=t.duration,
            most_likely=t.most_likely,
            predecessors=t.predecessors,
        )
        for t in payload.tasks
    ]
    result = cpm.compute_cpm(tasks)
    return jsonable_encoder(result)


@router.post("/mta")
def calc_mta(payload: MtaRequest):
    points = [ForecastPoint(p.report_date, p.forecast_date) for p in payload.points]
    result = analyze_milestone_trend(payload.baseline_date, points)
    return jsonable_encoder(result)


@router.post("/ccpm/buffer")
def calc_ccpm_buffer(payload: CcpmBufferRequest):
    tasks = [ccpm.ChainTask(t.code, t.most_likely, t.pessimistic) for t in payload.tasks]
    return {"buffer_ssq": ccpm.ssq_buffer(tasks)}


@router.post("/ccpm/fever")
def calc_ccpm_fever(payload: CcpmFeverRequest):
    return jsonable_encoder(
        ccpm.fever_chart_zone(payload.chain_complete_pct, payload.buffer_consumed_pct)
    )
