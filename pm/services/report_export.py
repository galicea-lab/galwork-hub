"""Renderowanie formularza (schemat + dane) do drukowalnego dokumentu HTML.

Prosty, zalezny wylacznie od typu sekcji generator — bez zewnetrznego silnika
szablonow. Wynik jest samodzielnym plikiem HTML z CSS do wydruku (@media print).
"""
from __future__ import annotations

from html import escape
from typing import Any


def _fields_html(fields: list[dict], data: dict | None) -> str:
    data = data or {}
    rows = "".join(
        f"<tr><th>{escape(f['label'])}</th><td>{escape(str(data.get(f['key'], '') or ''))}</td></tr>"
        for f in fields
    )
    return f"<table class='fields'>{rows}</table>"


def _table_html(columns: list[dict], rows: list[dict] | None) -> str:
    rows = rows or []
    header = "".join(f"<th>{escape(c['label'])}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(row.get(c['key'], '') or ''))}</td>" for c in columns) + "</tr>"
        for row in rows
    ) or f"<tr>{'<td>&nbsp;</td>' * len(columns)}</tr>"
    return f"<table class='grid'><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def _checklist_html(items: list[dict], data: list[dict] | dict | None) -> str:
    checked_keys = set()
    if isinstance(data, dict):
        checked_keys = {k for k, v in data.items() if v}
    lis = "".join(
        f"<li><input type='checkbox' disabled {'checked' if it['key'] in checked_keys else ''}> "
        f"{escape(it['label'])}</li>"
        for it in items
    )
    return f"<ul class='checklist'>{lis}</ul>"


def _narrative_html(blocks: list[dict], data: dict | None) -> str:
    data = data or {}
    parts = "".join(
        f"<h4>{escape(b['label'])}</h4><blockquote>{escape(str(data.get(b['key'], '') or '...'))}</blockquote>"
        for b in blocks
    )
    return parts


def _signatures_html(roles: list[dict], data: dict | None) -> str:
    data = data or {}
    rows = "".join(
        f"<tr><th>{escape(r['label'])}</th>"
        f"<td>{escape(str((data.get(r['key']) or {}).get('osoba', '')))}</td>"
        f"<td>{escape(str((data.get(r['key']) or {}).get('data', '')))}</td>"
        f"<td class='sig'></td></tr>"
        for r in roles
    )
    return f"<table class='fields'><tr><th></th><th>Osoba</th><th>Data</th><th>Podpis</th></tr>{rows}</table>"


def render_form_html(form_def: dict, sections_data: dict[str, Any]) -> str:
    body_parts = [f"<h1>{escape(form_def['title'])}</h1>", f"<p class='phase'>{escape(form_def['phase'])}</p>"]

    for section in form_def["sections"]:
        key = section["key"]
        value = sections_data.get(key)
        title = f"<h3>{escape(section['title'])}</h3>" if section.get("title") else ""

        if section["type"] == "info":
            body_parts.append(f"{title}<div class='info'>{escape(section.get('markdown', ''))}</div>")
        elif section["type"] == "fields":
            body_parts.append(title + _fields_html(section["fields"], value))
        elif section["type"] == "table":
            body_parts.append(title + _table_html(section["columns"], value))
        elif section["type"] == "checklist":
            body_parts.append(title + _checklist_html(section["items"], value))
        elif section["type"] == "narrative":
            body_parts.append(title + _narrative_html(section["blocks"], value))
        elif section["type"] == "signatures":
            body_parts.append(title + _signatures_html(section["roles"], value))
        # sekcje typu "calc" pomijamy w wydruku — wynik prezentowany jest na dashboardzie

    body = "\n".join(body_parts)
    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>{escape(form_def['title'])}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .phase {{ color: #666; margin-top: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.5rem 0 1.5rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }}
  table.fields th {{ width: 30%; background: #f5f5f5; }}
  table.grid thead th {{ background: #f5f5f5; }}
  .checklist {{ list-style: none; padding-left: 0; }}
  .checklist li {{ margin: 0.3rem 0; }}
  blockquote {{ margin: 0 0 1rem; padding: 0.5rem 1rem; background: #fafafa; border-left: 3px solid #ccc; white-space: pre-wrap; }}
  td.sig {{ min-width: 8rem; }}
  @media print {{ body {{ margin: 0.5in; }} }}
</style>
</head>
<body>
{body}
</body>
</html>"""
