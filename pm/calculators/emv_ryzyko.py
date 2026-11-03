"""Kalkulator Oczekiwanej Wartosci Pienieznej (EMV) ryzyka i rezerwy budzetowej.

Port `formularze/skrypty/emv_ryzyko.py` (formularz 08 — Rejestr ryzyka).

EMV = P * I; P - prawdopodobienstwo (0..1), I - wplyw finansowy
(dodatni dla zagrozenia = strata, ujemny dla szansy = zysk).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Ryzyko:
    nazwa: str
    prawd: float
    wplyw: float

    @property
    def emv(self) -> float:
        return self.prawd * self.wplyw


def rezerwa(ryzyka: list[Ryzyko]) -> dict:
    """Suma EMV zagrozen, suma EMV szans oraz rezerwa netto."""
    emv_zagrozenia = sum(r.emv for r in ryzyka if r.wplyw > 0)
    emv_szanse = sum(r.emv for r in ryzyka if r.wplyw < 0)
    return {
        "emv_zagrozenia": emv_zagrozenia,
        "emv_szanse": emv_szanse,
        "rezerwa_netto": emv_zagrozenia + emv_szanse,
        "items": [
            {
                "nazwa": r.nazwa,
                "prawd": r.prawd,
                "wplyw": r.wplyw,
                "emv": r.emv,
                "typ": "zagrozenie" if r.wplyw > 0 else "szansa",
            }
            for r in ryzyka
        ],
    }
