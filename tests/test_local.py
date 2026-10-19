"""Local sanity checks — no network required.

Exercises: parse → round-trip write → re-parse equivalence, placeholder detection,
and simulated pull using a fake sheet (monkeypatched)."""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from locsync.xml_io import (
    parse_strings_xml, write_strings_xml, placeholders, StringEntry,
)


SRC = os.path.join(ROOT, "app/src/main/res/values/strings.xml")


def test_parse_source():
    entries = parse_strings_xml(SRC)
    by_key = {e.key: e for e in entries}
    assert by_key["app_name"].kind == "string"
    assert by_key["debug_label"].translatable is False
    assert "%1$s" in by_key["greeting"].value
    assert by_key["items_count"].kind == "plurals"
    assert set(by_key["items_count"].plurals) == {"one", "other"}
    assert by_key["colors"].kind == "string-array"
    assert by_key["colors"].array == ["Red", "Green", "Blue"]
    assert by_key["greeting"].comment == "Greeting shown on the home screen"
    print("OK parse_source")


def test_roundtrip():
    entries = parse_strings_xml(SRC)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "values", "strings.xml")
        write_strings_xml(out, entries)
        rt = parse_strings_xml(out)
    by_a = {e.key: e for e in entries}
    by_b = {e.key: e for e in rt}
    assert set(by_a) == set(by_b)
    # Greeting placeholders preserved
    assert placeholders(by_a["greeting"].value) == placeholders(by_b["greeting"].value)
    # Rich markup preserved
    assert "<b>" in by_b["rich"].value
    print("OK roundtrip")


def test_placeholders():
    assert placeholders("Hello %1$s you have %2$d msgs") == sorted(["%1$s", "%2$d"])
    assert placeholders("plain text") == []
    print("OK placeholders")


def test_pull_with_fake_sheet(monkeypatch=None):
    # Stub the SheetClient so pull() runs without credentials
    from locsync import core, sheet

    class FakeClient:
        def __init__(self, sid): pass
        def ensure_tab(self, tab): return 0
        def read_rows(self, tab):
            if tab == "tr":
                return [
                    sheet.Row(key="app_name", value="String Otomatik"),
                    sheet.Row(key="greeting", value="Merhaba, %1$s! %2$d yeni mesajınız var."),
                    sheet.Row(key="quoted", value=""),
                    sheet.Row(key="rich", value="<b>Android</b>'e hoş geldiniz"),
                    sheet.Row(key="whitespace", value="  dolgu  "),
                ]
            return []
        def replace_all(self, tab, rows): pass

    core.SheetClient = FakeClient  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        # Copy source xml into tmp res
        res_dir = os.path.join(tmp, "res")
        os.makedirs(os.path.join(res_dir, "values"))
        with open(SRC) as f, open(os.path.join(res_dir, "values", "strings.xml"), "w") as g:
            g.write(f.read())
        cfg = {
            "spreadsheet_id": "x", "source_locale": "en",
            "res_dir": res_dir, "locales": ["tr"], "fallback": "source",
        }
        out = core.pull(cfg)
        tr_xml = os.path.join(res_dir, "values-tr", "strings.xml")
        assert os.path.exists(tr_xml)
        entries = {e.key: e for e in parse_strings_xml(tr_xml)}
        assert "String Otomatik" in entries["app_name"].value
        # quoted was not translated → fallback to source
        assert "hi" in entries["quoted"].value
        # placeholder round-trip
        assert placeholders(entries["greeting"].value) == sorted(["%1$s", "%2$d"])
        print("OK pull_with_fake_sheet", out["summary"])


def test_push_backfills_from_xml():
    """push() fills empty sheet cells from a real xml translation, keeps existing
    sheet values, and never treats a source-equal fallback as a translation."""
    from locsync import core, sheet

    src_entries = {e.key: e for e in parse_strings_xml(SRC)}
    src_app = src_entries["app_name"].value or ""
    src_greeting = src_entries["greeting"].value or ""

    written = {}

    class FakeClient:
        def __init__(self, sid): pass
        def ensure_tab(self, tab): return 0
        def read_rows(self, tab):
            if tab == "tr":
                return [
                    sheet.Row(key="app_name", value=""),            # empty -> backfill
                    sheet.Row(key="greeting", value="Merhaba %1$s %2$d"),  # keep
                ]
            return []
        def replace_all(self, tab, rows):
            written[tab] = {r.key: r.value for r in rows}

    core.SheetClient = FakeClient  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        res_dir = os.path.join(tmp, "res")
        os.makedirs(os.path.join(res_dir, "values"))
        with open(SRC) as f, open(os.path.join(res_dir, "values", "strings.xml"), "w") as g:
            g.write(f.read())
        # on-disk tr translations: app_name real, greeting a fallback copy of source
        tr_entries = [
            StringEntry(key="app_name", kind="string", value="Uygulama Adı"),
            StringEntry(key="greeting", kind="string", value=src_greeting),
        ]
        write_strings_xml(os.path.join(res_dir, "values-tr", "strings.xml"), tr_entries)

        cfg = {
            "spreadsheet_id": "x", "source_locale": "en",
            "res_dir": res_dir, "locales": ["tr"], "fallback": "source",
        }
        out = core.push(cfg)

    tr = written["tr"]
    assert tr["app_name"] == "Uygulama Adı"          # backfilled from xml
    assert tr["greeting"] == "Merhaba %1$s %2$d"      # existing sheet value kept
    assert out["summary"]["tr"]["backfilled"] == 1
    # a hypothetical empty cell whose xml equals source must NOT be backfilled
    assert src_app != "Uygulama Adı"
    print("OK push_backfills_from_xml", out["summary"])


if __name__ == "__main__":
    test_parse_source()
    test_roundtrip()
    test_placeholders()
    test_pull_with_fake_sheet()
    test_push_backfills_from_xml()
    print("ALL TESTS PASSED")
