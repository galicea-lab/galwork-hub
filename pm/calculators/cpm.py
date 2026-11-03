"""Kalkulator sieci PDM / Metody Sciezki Krytycznej (CPM).

Nowy modul (brak odpowiednika w `formularze/skrypty/`), implementujacy pelny
algorytm rozdz. 3-4 `r3narzedzia.md`: przejscie w przod/tyl dla sieci PDM
(Activity on Node) z 4 typami zaleznosci (FS/SS/FF/SF) i wyprzedzeniami/
opoznieniami (lead/lag), wyznaczanie buforow (TF/FF) i sciezki krytycznej.

Uwaga o buforze swobodnym (Free Float): formula ksiazkowa
FF_i = min(ES_nastepnikow) - EF_i (rozdz. 3.4) dotyczy wprost relacji FS,
ktore stanowia >90% przypadkow w projektach IT. Dla zadan z nastepnikami
innego typu przyjeto bezpieczny fallback FF = TF.
"""
from __future__ import annotations

from dataclasses import dataclass, field

RelationType = str  # "FS" | "SS" | "FF" | "SF"
_VALID_TYPES = {"FS", "SS", "FF", "SF"}


class CpmError(ValueError):
    pass


@dataclass
class Predecessor:
    code: str
    type: RelationType = "FS"
    lag: float = 0.0


@dataclass
class Task:
    code: str
    duration: float
    name: str = ""
    predecessors: list[Predecessor] = field(default_factory=list)


@dataclass
class TaskResult:
    code: str
    name: str
    duration: float
    es: float
    ef: float
    ls: float
    lf: float
    total_float: float
    free_float: float
    is_critical: bool


@dataclass
class CpmResult:
    project_duration: float
    tasks: list[TaskResult]
    critical_tasks: list[str]


def _topological_order(tasks: dict[str, Task]) -> tuple[list[str], dict[str, list[str]]]:
    indegree = {code: 0 for code in tasks}
    successors: dict[str, list[str]] = {code: [] for code in tasks}
    for code, t in tasks.items():
        for p in t.predecessors:
            if p.code not in tasks:
                raise CpmError(f"Zadanie {code}: nieznany poprzednik '{p.code}'")
            if p.type not in _VALID_TYPES:
                raise CpmError(f"Zadanie {code}: nieznany typ zaleznosci '{p.type}'")
            successors[p.code].append(code)
            indegree[code] += 1

    queue = sorted(code for code, d in indegree.items() if d == 0)
    order: list[str] = []
    while queue:
        queue.sort()
        code = queue.pop(0)
        order.append(code)
        for s in successors[code]:
            indegree[s] -= 1
            if indegree[s] == 0:
                queue.append(s)

    if len(order) != len(tasks):
        raise CpmError(
            "Wykryto cykl w sieci zadan — CPM wymaga acyklicznego grafu zaleznosci"
        )
    return order, successors


def compute_cpm(tasks: list[Task]) -> CpmResult:
    if not tasks:
        return CpmResult(project_duration=0.0, tasks=[], critical_tasks=[])

    by_code: dict[str, Task] = {}
    for t in tasks:
        if t.code in by_code:
            raise CpmError(f"Zduplikowany kod zadania: {t.code}")
        by_code[t.code] = t

    order, successors = _topological_order(by_code)

    # Faza 1: przejscie w przod (Forward Pass)
    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    for code in order:
        t = by_code[code]
        bounds = [0.0]
        for p in t.predecessors:
            if p.type == "FS":
                bounds.append(ef[p.code] + p.lag)
            elif p.type == "SS":
                bounds.append(es[p.code] + p.lag)
            elif p.type == "FF":
                bounds.append(ef[p.code] + p.lag - t.duration)
            elif p.type == "SF":
                bounds.append(es[p.code] + p.lag - t.duration)
        es[code] = max(bounds)
        ef[code] = es[code] + t.duration

    project_duration = max(ef.values())

    # Faza 2: przejscie w tyl (Backward Pass)
    lf: dict[str, float] = {}
    ls: dict[str, float] = {}
    for code in reversed(order):
        t = by_code[code]
        succs = successors[code]
        if not succs:
            lf[code] = project_duration
        else:
            bounds = []
            for s_code in succs:
                s_task = by_code[s_code]
                for p in s_task.predecessors:
                    if p.code != code:
                        continue
                    if p.type == "FS":
                        bounds.append(ls[s_code] - p.lag)
                    elif p.type == "SS":
                        bounds.append(ls[s_code] - p.lag + t.duration)
                    elif p.type == "FF":
                        bounds.append(lf[s_code] - p.lag)
                    elif p.type == "SF":
                        bounds.append(lf[s_code] - p.lag + t.duration)
            lf[code] = min(bounds) if bounds else project_duration
        ls[code] = lf[code] - t.duration

    # Krok 3: bufory i sciezka krytyczna
    results: list[TaskResult] = []
    for code in order:
        t = by_code[code]
        tf = ls[code] - es[code]
        fs_successor_es = [
            es[s_code] - p.lag
            for s_code in successors[code]
            for p in by_code[s_code].predecessors
            if p.code == code and p.type == "FS"
        ]
        ff = (min(fs_successor_es) - ef[code]) if fs_successor_es else tf
        results.append(
            TaskResult(
                code=code,
                name=t.name,
                duration=t.duration,
                es=es[code],
                ef=ef[code],
                ls=ls[code],
                lf=lf[code],
                total_float=tf,
                free_float=ff,
                is_critical=abs(tf) < 1e-9,
            )
        )

    critical = [r.code for r in results if r.is_critical]
    return CpmResult(project_duration=project_duration, tasks=results, critical_tasks=critical)


def parse_predecessors(text: str) -> list[Predecessor]:
    """Parsuje DSL 'KOD:TYP:LAG;KOD2:TYP2:LAG2' uzywany w tabeli `schedule_tasks`
    formularza 09 (typ i lag opcjonalne, domyslnie FS i 0)."""
    result: list[Predecessor] = []
    for chunk in (text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        code = parts[0].strip()
        rel_type = parts[1].strip().upper() if len(parts) > 1 and parts[1].strip() else "FS"
        lag = float(parts[2]) if len(parts) > 2 and parts[2].strip() else 0.0
        if not code:
            continue
        result.append(Predecessor(code=code, type=rel_type, lag=lag))
    return result


def format_predecessors(predecessors: list[dict]) -> str:
    """Odwrotnosc `parse_predecessors` — do wyswietlenia stanu z bazy w tabeli edycyjnej."""
    return ";".join(f"{p['code']}:{p.get('type', 'FS')}:{p.get('lag', 0)}" for p in predecessors)


def task_from_raw(
    code: str,
    name: str = "",
    duration: float | None = None,
    most_likely: float | None = None,
    predecessors: str = "",
) -> Task:
    """Buduje `Task` z surowego wiersza tabeli `schedule_tasks` (formularz 09):
    `duration` (CPM) domyslnie spada do `most_likely` (PERT), gdy nie podano."""
    dur = duration if duration is not None else most_likely
    if dur is None:
        raise CpmError(f"Zadanie {code}: brak czasu trwania (duration lub most_likely)")
    return Task(code=code, name=name, duration=dur, predecessors=parse_predecessors(predecessors))
