"""Kalkulator Metody Wartosci Wypracowanej (Earned Value Management).

Port + rozszerzenie `formularze/skrypty/evm.py` (formularz 11 — Raport EVM),
o warianty prognoz EAC/ETC/TCPI opisane w rozdz. 3.8.3 `r3narzedzia.md`.

Wielkosci bazowe: BAC, PV, EV, AC.
    SV  = EV - PV                CV  = EV - AC
    SPI = EV / PV                CPI = EV / AC

Warianty prognozy koncowego kosztu (rozdz. 8.3):
    EAC_A (standardowy, liniowy) = BAC / CPI            ETC_A = EAC_A - AC
    EAC_B (addytywny)            = AC + (BAC - EV)       ETC_B = BAC - EV
    EAC_C (zlozony, CPI*SPI)     = AC + (BAC - EV) / (CPI * SPI)
    VAC = BAC - EAC (dla wybranego wariantu; domyslnie A)
    TCPI_BAC = (BAC - EV) / (BAC - AC)
    TCPI_EAC = (BAC - EV) / (EAC - AC)
"""
from __future__ import annotations

from dataclasses import dataclass

NAN = float("nan")


@dataclass
class EVMData:
    BAC: float
    PV: float
    EV: float
    AC: float

    def compute(self) -> dict[str, float]:
        sv = self.EV - self.PV
        cv = self.EV - self.AC
        spi = self.EV / self.PV if self.PV else NAN
        cpi = self.EV / self.AC if self.AC else NAN

        eac_a = self.BAC / cpi if cpi else NAN
        etc_a = eac_a - self.AC if cpi else NAN
        eac_b = self.AC + (self.BAC - self.EV)
        etc_b = self.BAC - self.EV
        eac_c = (
            self.AC + (self.BAC - self.EV) / (cpi * spi)
            if cpi and spi
            else NAN
        )

        vac = self.BAC - eac_a if cpi else NAN

        tcpi_bac = (
            (self.BAC - self.EV) / (self.BAC - self.AC)
            if (self.BAC - self.AC)
            else NAN
        )
        tcpi_eac = (
            (self.BAC - self.EV) / (eac_a - self.AC)
            if cpi and (eac_a - self.AC)
            else NAN
        )

        return {
            "BAC": self.BAC, "PV": self.PV, "EV": self.EV, "AC": self.AC,
            "SV": sv, "CV": cv, "SPI": spi, "CPI": cpi,
            "EAC_A": eac_a, "ETC_A": etc_a,
            "EAC_B": eac_b, "ETC_B": etc_b,
            "EAC_C": eac_c,
            "VAC": vac,
            "TCPI_BAC": tcpi_bac, "TCPI_EAC": tcpi_eac,
        }
