"""Google Sheets client wrapper.

Each locale is a tab. Row 1 is header: key | value | comment | translated.
Column A is key-locked (source-of-truth = strings.xml).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["key", "value"]


@dataclass
class Row:
    key: str
    value: str = ""

    @property
    def translated(self) -> bool:
        return bool(self.value)


def _client():
    from google.oauth2 import service_account  # lazy
    from googleapiclient.discovery import build  # lazy
    raw = os.environ.get("GOOGLE_SA_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SA_JSON env var not set")
    if raw.strip().startswith("{"):
        info = json.loads(raw)
    else:
        with open(raw, "r", encoding="utf-8") as f:
            info = json.load(f)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


class SheetClient:
    def __init__(self, spreadsheet_id: str):
        self.sid = spreadsheet_id
        self.svc = _client()

    # ---------- tabs ----------
    def list_tabs(self) -> Dict[str, int]:
        meta = self.svc.spreadsheets().get(spreadsheetId=self.sid).execute()
        return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    def ensure_tab(self, title: str) -> int:
        tabs = self.list_tabs()
        if title in tabs:
            return tabs[title]
        req = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
        resp = self.svc.spreadsheets().batchUpdate(spreadsheetId=self.sid, body=req).execute()
        sid = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        # Write header
        self.svc.spreadsheets().values().update(
            spreadsheetId=self.sid, range=f"'{title}'!A1:B1",
            valueInputOption="RAW", body={"values": [HEADER]},
        ).execute()
        return sid

    # ---------- read ----------
    def read_rows(self, tab: str) -> List[Row]:
        r = self.svc.spreadsheets().values().get(
            spreadsheetId=self.sid, range=f"'{tab}'!A2:B",
        ).execute()
        rows = []
        for raw in r.get("values", []):
            raw = raw + [""] * (2 - len(raw))
            key, value = raw[:2]
            if not key:
                continue
            rows.append(Row(key=key, value=value or ""))
        return rows

    # ---------- write ----------
    def replace_all(self, tab: str, rows: List[Row]) -> None:
        """Overwrite tab body (below header) with the given rows, in order."""
        self.ensure_tab(tab)
        self.svc.spreadsheets().values().clear(
            spreadsheetId=self.sid, range=f"'{tab}'!A2:B",
        ).execute()
        if not rows:
            return
        values = [[r.key, r.value] for r in rows]
        self.svc.spreadsheets().values().update(
            spreadsheetId=self.sid, range=f"'{tab}'!A2",
            valueInputOption="RAW", body={"values": values},
        ).execute()
