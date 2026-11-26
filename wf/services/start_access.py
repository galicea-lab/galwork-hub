# wf/services/start_access.py
# 2026-11-25: kto może uruchomić obieg z poziomu API (POST /process-instances).
"""
Start obiegu przez użytkownika.

  * administrator procesów - zawsze,
  * `galwork.start_groups` w process_model.json (np. `["student"]`) - członek
    którejś z grup,
  * bez `start_groups` - pracownik (`wf.dependencies.is_staff`,
    `[wf] staff_groups`).

Dotyczy tylko startu z API. Starty wewnętrzne (e-Doręczenia, faktury z ERPNext,
instancje potomne, szybkie akcje zadania) mają własne uprawnienia. Dodatkowe
warunki (redaktor aktów w repozytorium `dokumenty`, wdrożenie obiegu
w jednostce - `rollouts.decide_start`) sprawdza router.
"""
from __future__ import annotations

from wf.dependencies import in_groups, is_process_admin, is_staff
from wf.models import ProcessModelVersion
from wf.schemas.wf_schemas import CurrentUser


def start_groups(version: ProcessModelVersion) -> frozenset[str] | None:
  """`galwork.start_groups` wersji modelu (None, gdy brak)."""
  galwork = ((version.spec_json or {}).get('galwork') or {})
  groups = galwork.get('start_groups')
  if groups is None:
    return None
  return frozenset(str(g).strip().lstrip('/').split('/')[-1].lower() for g in groups if str(g).strip())


def can_start(version: ProcessModelVersion, user: CurrentUser) -> bool:
  if is_process_admin(user):
    return True
  groups = start_groups(version)
  if groups is not None:
    return in_groups(user, groups)
  return is_staff(user)
