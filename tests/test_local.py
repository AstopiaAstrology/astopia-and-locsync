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
    # Literal percent signs in prose are not placeholders
    assert placeholders("🎁 Tap to get your 50% discount gift") == []
    assert placeholders("🎁 50% de Desconto") == []
    assert placeholders("Get 50% off today") == []
    assert placeholders("🎁%50 indirim") == []
    # Real placeholders still detected
    assert placeholders("%d%% off, %-5s and %05.2f") == sorted(["%d", "%%", "%-5s", "%05.2f"])
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
    src_quoted = src_entries["quoted"].value or ""

    written = {}

    class FakeClient:
        def __init__(self, sid): pass
        def ensure_tab(self, tab): return 0
        def read_rows(self, tab):
            if tab == "tr":
                return [
                    sheet.Row(key="app_name", value=""),            # empty -> backfill
                    sheet.Row(key="greeting", value="ESKI %1$s %2$d"),     # differs -> update
                    sheet.Row(key="quoted", value="Sheette var"),   # xml fallback -> keep
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
        # on-disk tr translations:
        #  app_name  -> real, differs from source  (empty sheet  -> backfill)
        #  greeting  -> real, differs from source & sheet (sheet set -> update)
        #  quoted    -> fallback copy of source     (sheet set    -> keep)
        tr_entries = [
            StringEntry(key="app_name", kind="string", value="Uygulama Adı"),
            StringEntry(key="greeting", kind="string", value="YENI %1$s %2$d"),
            StringEntry(key="quoted", kind="string", value=src_quoted),
        ]
        write_strings_xml(os.path.join(res_dir, "values-tr", "strings.xml"), tr_entries)

        cfg = {
            "spreadsheet_id": "x", "source_locale": "en",
            "res_dir": res_dir, "locales": ["tr"], "fallback": "source",
        }
        out = core.push(cfg)

    tr = written["tr"]
    assert tr["app_name"] == "Uygulama Adı"          # backfilled into empty cell
    assert tr["greeting"] == "YENI %1$s %2$d"         # existing cell UPDATED from xml
    assert tr["quoted"] == "Sheette var"              # xml was fallback -> sheet kept
    assert out["summary"]["tr"]["backfilled"] == 1
    assert out["summary"]["tr"]["updated"] == 1
    # sanity: the "real" values genuinely differ from source
    assert src_app != "Uygulama Adı" and src_greeting != "YENI %1$s %2$d"
    print("OK push_syncs_from_xml", out["summary"])


def test_validate_removed_keys_do_not_fail():
    """A key deleted from source xml but still in the sheet (the post-merge push
    has not run yet) is a warning, not an error — unless --strict."""
    from locsync import core, sheet

    class FakeClient:
        def __init__(self, sid): pass
        def ensure_tab(self, tab): return 0
        def read_rows(self, tab):
            return [
                sheet.Row(key="app_name", value="Uygulama"),
                sheet.Row(key="greeting", value="Merhaba %1$s %2$d"),
                sheet.Row(key="deleted_in_pr", value="Silindi"),
            ]
        def replace_all(self, tab, rows): pass

    core.SheetClient = FakeClient  # type: ignore
    cfg = {
        "spreadsheet_id": "x", "source_locale": "en",
        "res_dir": os.path.dirname(os.path.dirname(SRC)), "locales": ["tr"],
    }

    out, code = core.validate(cfg)
    assert code == 0, out["errors"]
    assert any("deleted_in_pr" in w for w in out["warnings"])

    out, code = core.validate(cfg, strict=True)
    assert code == 1
    assert any("deleted_in_pr" in e for e in out["errors"])

    # Placeholder mismatches still fail without --strict.
    class BadPlaceholder(FakeClient):
        def read_rows(self, tab):
            return [sheet.Row(key="greeting", value="Merhaba %1$s")]

    core.SheetClient = BadPlaceholder  # type: ignore
    out, code = core.validate(cfg)
    assert code == 1 and any("placeholder mismatch" in e for e in out["errors"])
    print("OK validate_removed_keys_do_not_fail")


def test_pull_never_writes_source_and_push_mirrors_source():
    """With the source locale listed in `locales`, pull must leave
    values/strings.xml untouched (it keeps translatable=false entries and newer
    text), and push must overwrite the source tab with the xml text."""
    from locsync import core, sheet

    written = {}

    class FakeClient:
        def __init__(self, sid): pass
        def ensure_tab(self, tab): return 0
        def read_rows(self, tab):
            # The sheet holds a stale copy of the source text.
            return [sheet.Row(key="app_name", value="Old source text")]
        def replace_all(self, tab, rows):
            written[tab] = {r.key: r.value for r in rows}

    core.SheetClient = FakeClient  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        res_dir = os.path.join(tmp, "res")
        os.makedirs(os.path.join(res_dir, "values"))
        src_path = os.path.join(res_dir, "values", "strings.xml")
        with open(SRC) as f, open(src_path, "w") as g:
            g.write(f.read())
        with open(src_path) as f:
            before = f.read()
        cfg = {
            "spreadsheet_id": "x", "source_locale": "en",
            "res_dir": res_dir, "locales": ["en", "tr"], "fallback": "source",
        }

        out = core.pull(cfg)
        with open(src_path) as f:
            assert f.read() == before, "pull rewrote the source xml"
        assert "en" not in out["summary"]
        assert os.path.exists(os.path.join(res_dir, "values-tr", "strings.xml"))

        core.push(cfg)
        src_app = {e.key: e for e in parse_strings_xml(src_path)}["app_name"].value
        assert written["en"]["app_name"] == src_app  # stale sheet text replaced
        assert written["tr"]["app_name"] == "Old source text"  # translations kept
    print("OK pull_never_writes_source_and_push_mirrors_source")


if __name__ == "__main__":
    test_parse_source()
    test_roundtrip()
    test_placeholders()
    test_pull_with_fake_sheet()
    test_push_backfills_from_xml()
    test_validate_removed_keys_do_not_fail()
    test_pull_never_writes_source_and_push_mirrors_source()
    print("ALL TESTS PASSED")
