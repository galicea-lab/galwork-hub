"""Kalkulator kodu DPCI (Darnall-Preston Complexity Index).

Port `formularze/skrypty/dpci.py` (formularz 06 — Profilowanie projektu).
"""
from __future__ import annotations

import math

CATEGORIES: dict[str, list[str]] = {
    "Zewnetrzne (External)": ["Rozmiar", "Czas trwania", "Dostepnosc zasobow"],
    "Wewnetrzne (Internal)": [
        "Jasnosc celow",
        "Jasnosc zakresu",
        "Zlozonosc organizacyjna",
        "Zgoda interesariuszy",
    ],
    "Technologiczne (Technological)": ["Nowosc technologii", "Znajomosc przez zespol"],
    "Srodowiskowe (Environmental)": [
        "Prawne (RODO/NIS2/DORA)",
        "Kulturowe",
        "Polityczne (wplyw zarzadu)",
        "Ekologiczne (Green IT)",
    ],
}


def round_half_up(x: float) -> int:
    """Zaokraglenie 'od polowy w gore' (0.5 -> 1; -0.5 -> 0)."""
    return int(math.floor(x + 0.5))


def compute_dpci(scores: dict[str, list[int]]) -> dict:
    """Zwraca liste wierszy (kategoria, ocena, anomalia) oraz sformatowany kod."""
    rows = []
    for cat, attrs in CATEGORIES.items():
        vals = scores.get(cat, [])
        if len(vals) != len(attrs):
            raise ValueError(
                f"Kategoria '{cat}' wymaga {len(attrs)} ocen, otrzymano {len(vals)}"
            )
        mean = sum(vals) / len(vals)
        rounded = round_half_up(mean)
        anomaly = any(v - mean >= 2.0 for v in vals)
        rows.append({"category": cat, "score": rounded, "anomaly": anomaly, "mean": mean})
    code = ".".join(f"[{r['score']}]" if r["anomaly"] else str(r["score"]) for r in rows)
    return {"rows": rows, "code": code}
