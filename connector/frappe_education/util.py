# coding: utf-8

"""Drobne funkcje pomocnicze """

from __future__ import annotations


def ni(tytul: str | None, imie_i: str | None, imie_ii: str | None, nazwisko: str | None) -> str:
    """Składa 'Tytuł Imię Imię2 Nazwisko' (puste składniki pomijane) -
    """
    s = (tytul + " ") if tytul else ""
    if imie_i:
        s += imie_i + " "
    if imie_ii:
        s += imie_ii + " "
    if nazwisko:
        return s + nazwisko
    return s


def rok_studiow(nr_semestru: int) -> int:
    """Rok studiów na podstawie numeru semestru (1,2 -> I; 3,4 -> II; ...)."""
    return ((float(nr_semestru) / 2) + 0.5).__floor__()


def rok_akademicki(rok: int) -> str:
    """'2024/2025' na podstawie roku początku roku akademickiego."""
    return f"{int(rok)}/{int(rok) + 1}"


_RZYMSKIE = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def r_cyfra(n: int | None) -> str:
    """Liczba jako cyfra rzymska,
    od I do XII"""
    if not n or n < 0 or n >= len(_RZYMSKIE):
        return str(n or "")
    return _RZYMSKIE[n]
