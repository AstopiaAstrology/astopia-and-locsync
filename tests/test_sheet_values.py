"""Sheet cells that break Android strings: outer quotes and iOS %@ placeholders.

pull must repair them; validate must flag them. No network required."""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from locsync import core, sheet
from locsync.xml_io import (
    clean_value, convert_ios_placeholders, has_ios_placeholder,
    is_wrapped_in_quotes, parse_strings_xml, render_value_inner, unwrap_quotes,
)

SOURCE_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="ask_our">"Danışmanlarımıza "</string>
    <string name="have_account">Hesabın var mı? Giriş yap &gt;</string>
    <string name="invite_gift_desc">Kayıt olduklarında 50 kredi kazansın.</string>
    <string name="greeting">Merhaba, %1$s! %2$d yeni mesaj.</string>
    <string name="said_yes">\\"Evet\\" dedi</string>
</resources>
"""


def _res_dir(tmp: str) -> str:
    res_dir = os.path.join(tmp, "res")
    os.makedirs(os.path.join(res_dir, "values"))
    with open(os.path.join(res_dir, "values", "strings.xml"), "w", encoding="utf-8") as f:
        f.write(SOURCE_XML)
    return res_dir


def _fake_client(rows):
    class FakeClient:
        def __init__(self, sid): pass
        def ensure_tab(self, tab): return 0
        def read_rows(self, tab): return [sheet.Row(key=k, value=v) for k, v in rows.items()]
        def replace_all(self, tab, rows): pass
    return FakeClient


def _raw_xml_lines(path: str) -> dict:
    """{key: raw line} so tests check the exact xml text, not a re-parse."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if '<string name="' in line:
                key = line.split('name="', 1)[1].split('"', 1)[0]
                out[key] = line.strip()
    return out


# ---------- unit: quotes ----------

def test_quoted_cell_with_trailing_space():
    v = '"Consulta a nuestros "'
    assert is_wrapped_in_quotes(v)
    assert unwrap_quotes(v) == "Consulta a nuestros "
    # Re-wrapped by Android quoting, never escaped into \"…\"
    assert render_value_inner(v) == '"Consulta a nuestros "'


def test_quoted_cell_with_leading_and_trailing_space_kept_exactly():
    assert unwrap_quotes('"  dolgu  "') == "  dolgu  "
    assert render_value_inner('"  dolgu  "') == '"  dolgu  "'


def test_quoted_cell_without_space():
    assert render_value_inner('"Tu as un compte ? Connexion >"') == "Tu as un compte ? Connexion &gt;"


def test_inner_quotes_kept_escaped():
    assert not is_wrapped_in_quotes('Dijo "bəli"')
    assert render_value_inner('Dijo "bəli"') == 'Dijo \\"bəli\\"'
    assert render_value_inner('Dijo \\"bəli\\"') == 'Dijo \\"bəli\\"'
    # Two quoted words are not one outer pair.
    assert not is_wrapped_in_quotes('"bəli" və "xeyr"')
    assert render_value_inner('"bəli" və "xeyr"') == '\\"bəli\\" və \\"xeyr\\"'
    # An escaped closing quote is not a pair either.
    assert not is_wrapped_in_quotes('"son\\"')
    # Quotes inside an outer pair stay escaped once the pair is removed.
    assert render_value_inner('"Dijo \\"bəli\\" "') == '"Dijo \\"bəli\\" "'


def test_quoted_markup_keeps_its_spaces():
    assert render_value_inner('"<b>Görsel</b> "') == '"<b>Görsel</b> "'
    assert render_value_inner('"<b>Görsel</b>"') == "<b>Görsel</b>"


# ---------- unit: %@ ----------

def test_single_ios_placeholder():
    assert has_ios_placeholder("Recibirá %@ créditos")
    assert convert_ios_placeholders("Recibirá %@ créditos") == "Recibirá %s créditos"


def test_two_ios_placeholders_are_numbered():
    v = "Elle recevra %@ crédits, et tu en gagneras %@."
    assert convert_ios_placeholders(v) == "Elle recevra %1$s crédits, et tu en gagneras %2$s."


def test_positional_ios_placeholders_and_escaped_percent():
    assert convert_ios_placeholders("%2$@ y %1$@") == "%2$s y %1$s"
    assert not has_ios_placeholder("100%%@")
    assert convert_ios_placeholders("100%%@") == "100%%@"
    assert convert_ios_placeholders("no placeholders") == "no placeholders"


def test_clean_value_does_both():
    assert clean_value('"Gana %@ "') == "Gana %s "


# ---------- pull ----------

def test_pull_writes_clean_values():
    core.SheetClient = _fake_client({  # type: ignore
        "ask_our": '"Consulta a nuestros "',
        "have_account": '"Iniciar sesión > "',
        "invite_gift_desc": "Recibirá %@ créditos y tú ganarás %@.",
        "said_yes": 'Dijo "bəli"',
    })
    with tempfile.TemporaryDirectory() as tmp:
        res_dir = _res_dir(tmp)
        core.pull({"spreadsheet_id": "x", "source_locale": "tr",
                   "res_dir": res_dir, "locales": ["es"], "fallback": "source"})
        es = os.path.join(res_dir, "values-es", "strings.xml")
        lines = _raw_xml_lines(es)
        parsed = {e.key: e.value for e in parse_strings_xml(es)}

    assert lines["ask_our"] == '<string name="ask_our">"Consulta a nuestros "</string>'
    assert lines["have_account"] == '<string name="have_account">"Iniciar sesión &gt; "</string>'
    assert lines["invite_gift_desc"] == (
        '<string name="invite_gift_desc">Recibirá %1$s créditos y tú ganarás %2$s.</string>')
    assert lines["said_yes"] == '<string name="said_yes">Dijo \\"bəli\\"</string>'
    assert "\\\"" not in parsed["ask_our"]
    # Untranslated quoted source falls back without its quotes leaking either.
    assert lines["greeting"] == '<string name="greeting">Merhaba, %1$s! %2$d yeni mesaj.</string>'


def test_pull_fallback_of_quoted_source_is_not_escaped():
    core.SheetClient = _fake_client({})  # type: ignore
    with tempfile.TemporaryDirectory() as tmp:
        res_dir = _res_dir(tmp)
        core.pull({"spreadsheet_id": "x", "source_locale": "tr",
                   "res_dir": res_dir, "locales": ["es"], "fallback": "source"})
        lines = _raw_xml_lines(os.path.join(res_dir, "values-es", "strings.xml"))
    assert lines["ask_our"] == '<string name="ask_our">"Danışmanlarımıza "</string>'


# ---------- validate ----------

def _validate(rows):
    core.SheetClient = _fake_client(rows)  # type: ignore
    with tempfile.TemporaryDirectory() as tmp:
        res_dir = _res_dir(tmp)
        return core.validate({"spreadsheet_id": "x", "source_locale": "tr",
                              "res_dir": res_dir, "locales": ["es"]})


def test_validate_passes_clean_values():
    out, code = _validate({
        "ask_our": '"Consulta a nuestros "',  # source is quoted too -> warning only
        "have_account": "Iniciar sesión >",
        "invite_gift_desc": "Recibirá 50 créditos.",
        "greeting": "Hola, %1$s! %2$d mensajes nuevos.",
        "said_yes": 'Dijo \\"sí\\"',
    })
    assert code == 0, out["errors"]
    assert any("ask_our" in w and "quoted" in w for w in out["warnings"])


def test_validate_fails_on_ios_placeholder():
    out, code = _validate({"greeting": "Hola, %@! %2$d mensajes nuevos."})
    assert code == 1
    assert any("iOS placeholder" in e and "greeting" in e for e in out["errors"])


def test_validate_fails_on_quoted_value_when_source_is_not():
    out, code = _validate({"have_account": '"Iniciar sesión > "'})
    assert code == 1
    assert any("have_account" in e and "wrapped in quotes" in e for e in out["errors"])


def test_validate_fails_on_placeholders_without_source_placeholders():
    out, code = _validate({"invite_gift_desc": "Recibirá %@ créditos y tú ganarás %@."})
    assert code == 1
    assert any("invite_gift_desc" in e and "source has none" in e for e in out["errors"])
    assert any("iOS placeholder" in e for e in out["errors"])
    # Android placeholders without source placeholders fail the same way.
    out, code = _validate({"invite_gift_desc": "Recibirá %d créditos."})
    assert code == 1
    assert any("source has none" in e for e in out["errors"])


def test_validate_still_fails_on_placeholder_mismatch():
    out, code = _validate({"greeting": "Hola, %1$s!"})
    assert code == 1
    assert any("placeholder mismatch" in e for e in out["errors"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("ALL TESTS PASSED")
