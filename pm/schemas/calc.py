from datetime import date

from pydantic import BaseModel


class RoiNpvRequest(BaseModel):
    flows: list[float]
    rate: float = 0.08


class DpciRequest(BaseModel):
    scores: dict[str, list[int]]


class EmvRiskItem(BaseModel):
    nazwa: str
    prawd: float
    wplyw: float


class EmvRequest(BaseModel):
    risks: list[EmvRiskItem]


class PertTaskItem(BaseModel):
    nazwa: str
    O: float
    M: float
    P: float


class PertRequest(BaseModel):
    tasks: list[PertTaskItem]
    t_s: float


class EvmRequest(BaseModel):
    bac: float
    pv: float
    ev: float
    ac: float


class CpmPredecessorItem(BaseModel):
    code: str
    type: str = "FS"
    lag: float = 0.0


class CpmTaskItem(BaseModel):
    code: str
    name: str = ""
    duration: float
    predecessors: list[CpmPredecessorItem] = []


class CpmRequest(BaseModel):
    tasks: list[CpmTaskItem]


class RawScheduleTaskItem(BaseModel):
    """Surowy wiersz tabeli `schedule_tasks` formularza 09 (przed parsowaniem DSL)."""

    code: str
    name: str = ""
    duration: float | None = None
    most_likely: float | None = None
    predecessors: str = ""


class CpmFromTasksRequest(BaseModel):
    tasks: list[RawScheduleTaskItem]


class MtaForecastItem(BaseModel):
    report_date: date
    forecast_date: date


class MtaRequest(BaseModel):
    baseline_date: date | None = None
    points: list[MtaForecastItem]


class CcpmBufferTaskItem(BaseModel):
    code: str
    most_likely: float
    pessimistic: float


class CcpmBufferRequest(BaseModel):
    tasks: list[CcpmBufferTaskItem]


class CcpmFeverRequest(BaseModel):
    chain_complete_pct: float
    buffer_consumed_pct: float
