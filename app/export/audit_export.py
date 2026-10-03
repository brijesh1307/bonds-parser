"""Audit log export for auditors: CSV, Excel or JSON (oldest record first).

Same columns in every format; `details` is written as compact JSON text in CSV and Excel.
Text that a spreadsheet would run as a formula is neutralised.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

COLUMNS = (
    "seq", "occurred_at", "action", "outcome", "error", "actor_type", "actor_id", "actor_name",
    "entity_type", "entity_id", "details", "request_id", "ip_address", "user_agent", "event_id",
    "prev_hash", "row_hash",
)  # fmt: skip


def _cell(value: Any) -> Any:
    if isinstance(value, dict):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def to_csv(records: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in records:
        w.writerow([_cell(r.get(c)) for c in COLUMNS])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens UTF-8 correctly


def to_xlsx(records: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.create_sheet("Audit", 0)
    wb.remove(wb.worksheets[1])
    ws.append(list(COLUMNS))
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3B63")
    for r in records:
        ws.append([_cell(r.get(c)) for c in COLUMNS])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_json(records: list[dict[str, Any]]) -> bytes:
    return (json.dumps(records, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
