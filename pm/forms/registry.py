"""Ladowanie i serwowanie schematow formularzy z `app/forms/definitions/*.json`.

Schematy sa generowane przez `scripts/build_form_definitions.py` na podstawie
`formularze/*.md` (repozytorium ksiazki) — patrz docstring tego skryptu.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


class FormNotFoundError(KeyError):
    pass


@lru_cache
def _load_all() -> dict[str, dict]:
    forms: dict[str, dict] = {}
    for path in sorted(DEFINITIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        forms[data["slug"]] = data
    return forms


def list_forms() -> list[dict]:
    """Lekka lista (slug/title/phase) do katalogu formularzy — bez pelnych sekcji."""
    return [
        {"slug": f["slug"], "title": f["title"], "phase": f["phase"]}
        for f in _load_all().values()
    ]


def get_form(slug: str) -> dict:
    forms = _load_all()
    if slug not in forms:
        raise FormNotFoundError(slug)
    return forms[slug]
