"""push / pull / validate operations."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from .sheet import Row, SheetClient
from .xml_io import (
    StringEntry,
    parse_strings_xml,
    placeholders,
    write_strings_xml,
)


def _source_xml_path(res_dir: str) -> str:
    return os.path.join(res_dir, "values", "strings.xml")


def _locale_xml_path(res_dir: str, locale: str, source_locale: str) -> str:
    sub = "values" if locale == source_locale else f"values-{locale}"
    return os.path.join(res_dir, sub, "strings.xml")


def load_source(res_dir: str) -> List[StringEntry]:
    entries = parse_strings_xml(_source_xml_path(res_dir))
    return [e for e in entries if e.translatable]


def load_all_source_keys(res_dir: str) -> set:
    """All keys in source xml, including translatable=false — used to
    distinguish 'orphan in sheet' from 'exists but not translatable'."""
    return {e.key for e in parse_strings_xml(_source_xml_path(res_dir))}


# ---------------- push ----------------

def _load_locale_translations(res_dir: str, locale: str, source_locale: str) -> Dict[str, str]:
    """Read on-disk values-<locale>/strings.xml -> {key: value} for <string> entries."""
    xml_path = _locale_xml_path(res_dir, locale, source_locale)
    out: Dict[str, str] = {}
    if not os.path.exists(xml_path):
        return out
    for e in parse_strings_xml(xml_path):
        if e.kind == "string" and e.translatable and e.value:
            out[e.key] = e.value
    return out


def push(config: dict) -> dict:
    """XML -> Sheet. Add missing keys, remove obsolete, sync translations.

    A *real* translation in the on-disk values-<locale>/strings.xml — present and
    different from the source value (an equal value is a fallback copy, not a
    translation) — is written to the sheet: it backfills an empty cell AND
    updates an existing cell whose value differs. When xml has no real
    translation (missing, or a source-equal fallback) the current sheet value is
    kept untouched, so a real sheet translation is never clobbered by a fallback.
    """
    src = load_source(config["res_dir"])
    # Only push simple <string> entries to the sheet. Plurals/arrays stay in xml.
    src_strings = [e for e in src if e.kind == "string"]
    source_order = [e.key for e in src_strings]
    source_meta = {e.key: e for e in src_strings}
    all_keys = load_all_source_keys(config["res_dir"])  # includes translatable=false

    sc = SheetClient(config["spreadsheet_id"])
    summary: Dict[str, dict] = {}
    warnings: List[str] = []

    for locale in config["locales"]:
        sc.ensure_tab(locale)
        existing = {r.key: r for r in sc.read_rows(locale)}
        is_source = locale == config["source_locale"]
        xml_tr = {} if is_source else _load_locale_translations(
            config["res_dir"], locale, config["source_locale"]
        )

        def xml_translation(key: str) -> str:
            """A real translation from xml for this key, else empty."""
            val = xml_tr.get(key, "")
            if not val:
                return ""
            if val == (source_meta[key].value or ""):
                return ""  # equal to source => fallback copy, not a translation
            return val

        for k in existing:
            if k in source_meta:
                continue
            if k in all_keys:
                warnings.append(f"[{locale}] dropping non-translatable key from sheet: {k!r}")
            else:
                warnings.append(f"[{locale}] stray key not in source xml: {k!r} — dropping")

        merged: List[Row] = []
        translated = 0
        missing = 0
        backfilled = 0
        updated = 0
        for key in source_order:
            src_entry = source_meta[key]
            sheet_val = existing[key].value if key in existing else ""
            if is_source:
                # Source locale always mirrors the source xml value.
                value = sheet_val or (src_entry.value or "")
            else:
                xml_val = xml_translation(key)
                if xml_val:
                    # A real xml translation wins — it fills an empty cell AND
                    # updates an existing one that differs (don't skip it).
                    value = xml_val
                    if not sheet_val:
                        backfilled += 1
                    elif sheet_val != xml_val:
                        updated += 1
                else:
                    # No real xml translation (missing or a source-equal fallback)
                    # -> keep whatever the sheet already has, never clobber it.
                    value = sheet_val

            merged.append(Row(key=key, value=value))
            if value:
                translated += 1
            else:
                missing += 1

        if backfilled:
            warnings.append(f"[{locale}] backfilled {backfilled} translation(s) from xml into empty sheet cells")
        if updated:
            warnings.append(f"[{locale}] updated {updated} existing sheet cell(s) from changed xml translations")

        sc.replace_all(locale, merged)
        summary[locale] = {
            "total": len(merged), "translated": translated,
            "missing": missing, "backfilled": backfilled, "updated": updated,
        }

    return {"op": "push", "summary": summary, "warnings": warnings}


# ---------------- seed ----------------

def seed(config: dict) -> dict:
    """One-time: upload existing values-<locale>/strings.xml translations to Sheet.

    For each locale, reads the on-disk translated xml and populates the Sheet
    tab so future pulls have the real translations instead of falling back to
    source. Safe to re-run: overwrites sheet with xml (xml wins).
    """
    src = load_source(config["res_dir"])
    src_strings = [e for e in src if e.kind == "string"]
    source_order = [e.key for e in src_strings]
    src_by_key = {e.key: e for e in src_strings}

    sc = SheetClient(config["spreadsheet_id"])
    summary: Dict[str, dict] = {}
    warnings: List[str] = []

    for locale in config["locales"]:
        sc.ensure_tab(locale)
        xml_path = _locale_xml_path(config["res_dir"], locale, config["source_locale"])
        locale_by_key: Dict[str, str] = {}
        if os.path.exists(xml_path):
            for e in parse_strings_xml(xml_path):
                if e.kind == "string" and e.translatable:
                    locale_by_key[e.key] = e.value or ""
        else:
            warnings.append(f"[{locale}] xml file not found at {xml_path} — tab will be empty")

        rows: List[Row] = []
        translated = 0
        missing = 0
        for key in source_order:
            val = locale_by_key.get(key, "")
            # For source locale, always take source xml value
            if locale == config["source_locale"]:
                val = src_by_key[key].value or ""
            rows.append(Row(key=key, value=val))
            if val:
                translated += 1
            else:
                missing += 1

        sc.replace_all(locale, rows)
        summary[locale] = {"total": len(rows), "translated": translated, "missing": missing}

    return {"op": "seed", "summary": summary, "warnings": warnings}


# ---------------- pull ----------------

def pull(config: dict) -> dict:
    src = load_source(config["res_dir"])
    src_by_key = {e.key: e for e in src if e.kind == "string"}
    # Non-string entries (plurals, string-array) stay in source xml only for now.
    plurals_and_arrays = [e for e in src if e.kind != "string"]
    source_order = [e.key for e in src if e.kind == "string"]

    sc = SheetClient(config["spreadsheet_id"])
    summary: Dict[str, dict] = {}
    warnings: List[str] = []
    fallback_mode = config.get("fallback", "source")  # "source" | "skip"

    for locale in config["locales"]:
        rows = {r.key: r for r in sc.read_rows(locale)}
        entries: List[StringEntry] = []
        translated = 0
        missing = 0
        for key in source_order:
            src_entry = src_by_key[key]
            r = rows.get(key)
            if r and r.value:
                entries.append(StringEntry(
                    key=key, kind="string", value=r.value,
                    comment=src_entry.comment, translatable=True,
                ))
                translated += 1
            else:
                missing += 1
                if fallback_mode == "source":
                    entries.append(StringEntry(
                        key=key, kind="string", value=src_entry.value or "",
                        comment=src_entry.comment, translatable=True,
                    ))
                # else: skip
        # Append plurals/arrays untouched for non-source locales too (fallback to source content)
        entries.extend(plurals_and_arrays)

        out_path = _locale_xml_path(config["res_dir"], locale, config["source_locale"])
        write_strings_xml(out_path, entries)
        summary[locale] = {
            "total": len(source_order), "translated": translated, "missing": missing,
        }

    return {"op": "pull", "summary": summary, "warnings": warnings}


# ---------------- validate ----------------

def validate(config: dict, strict: bool = False) -> Tuple[dict, int]:
    """Validate source ↔ sheet consistency.

    Hard errors (always fail): placeholder mismatch, orphan keys in sheet.
    Soft (fail only in --strict): missing translations per locale.
    New-in-PR keys never fail: post-merge push will add them.
    """
    src = load_source(config["res_dir"])
    src_strings = {e.key: e for e in src if e.kind == "string"}
    all_keys = load_all_source_keys(config["res_dir"])

    sc = SheetClient(config["spreadsheet_id"])
    summary: Dict[str, dict] = {}
    errors: List[str] = []
    warnings: List[str] = []

    for locale in config["locales"]:
        rows = {r.key: r for r in sc.read_rows(locale)}

        for k in rows:
            if k in src_strings:
                continue
            if k in all_keys:
                warnings.append(f"[{locale}] non-translatable key in sheet (will be dropped on next push): {k}")
            else:
                errors.append(f"[{locale}] orphan key in sheet not in source xml: {k}")

        translated = 0
        missing = 0
        new_in_pr = 0
        for k, src_e in src_strings.items():
            r = rows.get(k)
            if r is None:
                new_in_pr += 1
                missing += 1
                continue
            if not r.value:
                missing += 1
                continue
            translated += 1
            src_ph = placeholders(src_e.value or "")
            tgt_ph = placeholders(r.value)
            if src_ph != tgt_ph:
                errors.append(
                    f"[{locale}] placeholder mismatch for {k!r}: "
                    f"source={src_ph} target={tgt_ph}"
                )
        if new_in_pr:
            warnings.append(f"[{locale}] {new_in_pr} new key(s) not yet in sheet — will be pushed post-merge")
        if strict and missing:
            errors.append(f"[{locale}] {missing} untranslated keys (strict mode)")
        summary[locale] = {
            "total": len(src_strings), "translated": translated, "missing": missing,
        }

    result = {"op": "validate", "summary": summary,
              "errors": errors, "warnings": warnings}
    return result, (1 if errors else 0)


# ---------------- entry ----------------

def load_config(path: str = "locsync.config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
