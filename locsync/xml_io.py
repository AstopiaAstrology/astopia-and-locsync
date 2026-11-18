"""Parse and emit Android strings.xml preserving plurals/arrays/CDATA/placeholders."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from lxml import etree

# The space flag ("% d") is deliberately not supported: Android strings never use it, and
# allowing it turns literal percent signs in prose ("50% discount", "50% de desconto")
# into false placeholders that fail validation.
PLACEHOLDER_RE = re.compile(r"%(?:\d+\$)?[-#+0,(]?\d*(?:\.\d+)?[bBhHsScCdoxXeEfgGaAtTn%]")


@dataclass
class StringEntry:
    key: str
    kind: str  # "string" | "plurals" | "string-array"
    # For "string": value is the inner XML (children serialized) as source-preserving text
    value: Optional[str] = None
    comment: Optional[str] = None
    translatable: bool = True
    # For "plurals": {quantity: inner_xml}
    plurals: Dict[str, str] = field(default_factory=dict)
    # For "string-array": [inner_xml, ...]
    array: List[str] = field(default_factory=list)


def _inner_xml(elem: etree._Element) -> str:
    """Serialize element's inner content (text + children) as XML string."""
    parts: List[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(etree.tostring(child, encoding="unicode", with_tail=True))
    return "".join(parts)


def parse_strings_xml(path: str) -> List[StringEntry]:
    """Parse a values*/strings.xml file. Returns entries in document order."""
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
    tree = etree.parse(path, parser)
    root = tree.getroot()
    entries: List[StringEntry] = []
    pending_comment: Optional[str] = None
    for node in root.iterchildren():
        if isinstance(node, etree._Comment):
            pending_comment = node.text.strip() if node.text else None
            continue
        tag = node.tag
        if tag == "string":
            e = StringEntry(
                key=node.get("name"),
                kind="string",
                value=_inner_xml(node),
                comment=pending_comment,
                translatable=(node.get("translatable", "true").lower() != "false"),
            )
            entries.append(e)
        elif tag == "plurals":
            plurals = {}
            for item in node.findall("item"):
                q = item.get("quantity")
                if q:
                    plurals[q] = _inner_xml(item)
            entries.append(StringEntry(
                key=node.get("name"), kind="plurals",
                plurals=plurals, comment=pending_comment,
                translatable=(node.get("translatable", "true").lower() != "false"),
            ))
        elif tag == "string-array":
            arr = [_inner_xml(item) for item in node.findall("item")]
            entries.append(StringEntry(
                key=node.get("name"), kind="string-array",
                array=arr, comment=pending_comment,
                translatable=(node.get("translatable", "true").lower() != "false"),
            ))
        pending_comment = None
    return entries


# ---------- Emission ----------

_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _needs_quoting(s: str) -> bool:
    if not s:
        return False
    # Leading/trailing whitespace or contains @/? or newline → quote-wrap
    if s != s.strip():
        return True
    if s.startswith(("@", "?")):
        return True
    return False


def _escape_android_value(raw_inner: str) -> str:
    """Escape a plain string (no inline tags) for Android strings.xml.

    - Escape XML specials & < >
    - Escape unescaped ' and "
    - Convert real newlines to \\n
    - Wrap in double quotes if leading/trailing whitespace or starts with @/?
    """
    if raw_inner is None:
        return ""
    s = raw_inner
    # Preserve existing backslash escapes (\n \t \' \" \\)
    # We'll re-escape only bare chars.
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(ch)
            out.append(s[i + 1])
            i += 2
            continue
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif ch == "'":
            out.append("\\'")
        elif ch == '"':
            out.append("\\\"")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
        i += 1
    escaped = "".join(out)
    if _needs_quoting(s):
        escaped = f"\"{escaped}\""
    return escaped


# ---------- Sheet value clean-up ----------

# iOS object placeholders: "%@" and positional "%1$@". "%%" is matched first so an
# escaped percent sign ("%%@") is never read as a placeholder.
_IOS_TOKEN_RE = re.compile(r"%%|%(\d+\$)?@")


def has_ios_placeholder(s: str) -> bool:
    return any(m.group(0) != "%%" for m in _IOS_TOKEN_RE.finditer(s or ""))


def convert_ios_placeholders(s: str) -> str:
    """Turn iOS "%@" / "%1$@" into Android "%s" / "%1$s".

    A single non-positional "%@" becomes "%s". When the value has several
    placeholders, each "%@" gets its position among all non-positional
    placeholders ("%1$s", "%2$s", …) so the argument order stays as written.
    """
    if not has_ios_placeholder(s):
        return s
    # Count every non-positional placeholder (Android ones and %@) in order.
    tokens = [m for m in re.finditer(r"%%|%\d+\$@|%@|" + PLACEHOLDER_RE.pattern, s)]
    seq = [m for m in tokens if m.group(0) != "%%" and "$" not in m.group(0)]
    numbered = len(seq) > 1
    index = {m.start(): i + 1 for i, m in enumerate(seq)}
    out: List[str] = []
    last = 0
    for m in tokens:
        tok = m.group(0)
        if not tok.endswith("@"):
            continue
        out.append(s[last:m.start()])
        if "$" in tok:
            out.append(tok[:-1] + "s")
        elif numbered:
            out.append(f"%{index[m.start()]}$s")
        else:
            out.append("%s")
        last = m.end()
    out.append(s[last:])
    return "".join(out)


def _is_escaped(s: str, pos: int) -> bool:
    """True if the char at pos is preceded by an odd number of backslashes."""
    n = 0
    while pos - 1 - n >= 0 and s[pos - 1 - n] == "\\":
        n += 1
    return n % 2 == 1


def is_wrapped_in_quotes(s: str) -> bool:
    """True if the whole value is one pair of bare double quotes: "…".

    The closing quote must not be backslash-escaped and the inside must hold no
    other bare double quote, so "a" and "b" (two quoted words) is not a pair.
    """
    if not s or len(s) < 2 or s[0] != '"' or s[-1] != '"' or _is_escaped(s, len(s) - 1):
        return False
    inner = s[1:-1]
    return not any(ch == '"' and not _is_escaped(inner, i) for i, ch in enumerate(inner))


def unwrap_quotes(s: str) -> str:
    """Drop an outer "…" pair, keeping the inner text exactly (spaces included).

    Translators (and the source column, which mirrors Android's own quoting)
    wrap a value in quotes to keep leading/trailing spaces. Escaping those
    quotes would print them on screen; the emitter re-adds Android quoting when
    the inner text needs it.
    """
    return s[1:-1] if is_wrapped_in_quotes(s) else s


def clean_value(s: str) -> str:
    """Normalise a sheet (or source) value before it is written to xml."""
    return convert_ios_placeholders(unwrap_quotes(s))


def _looks_like_xml_markup(s: str) -> bool:
    return "<" in s and ">" in s


def render_value_inner(raw: str) -> str:
    """Return inner XML suitable for placing between <string>…</string>.

    If the incoming string appears to already contain inline markup (e.g. <b>),
    we assume the translator provided valid XML and pass it through with only
    ampersand normalization. Otherwise we escape as plain text.
    """
    if raw is None:
        return ""
    raw = clean_value(raw)
    if _looks_like_xml_markup(raw):
        # Attempt to parse as fragment; if it fails, fall back to escaping.
        try:
            etree.fromstring(f"<r>{raw}</r>")
            # Markup passes through as is, so re-add the quotes that keep
            # leading/trailing spaces (clean_value may have just removed them).
            return f'"{raw}"' if raw != raw.strip() else raw
        except etree.XMLSyntaxError:
            pass
    return _escape_android_value(raw)


def write_strings_xml(path: str, entries: List[StringEntry]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines: List[str] = ['<?xml version="1.0" encoding="utf-8"?>', "<resources>"]
    for e in entries:
        if e.comment:
            lines.append(f"    <!-- {e.comment} -->")
        attrs = f' name="{e.key}"'
        if not e.translatable:
            attrs += ' translatable="false"'
        if e.kind == "string":
            lines.append(f"    <string{attrs}>{render_value_inner(e.value or '')}</string>")
        elif e.kind == "plurals":
            lines.append(f"    <plurals{attrs}>")
            for q, v in e.plurals.items():
                lines.append(f'        <item quantity="{q}">{render_value_inner(v)}</item>')
            lines.append("    </plurals>")
        elif e.kind == "string-array":
            lines.append(f"    <string-array{attrs}>")
            for v in e.array:
                lines.append(f"        <item>{render_value_inner(v)}</item>")
            lines.append("    </string-array>")
    lines.append("</resources>")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def placeholders(s: str) -> List[str]:
    return sorted(PLACEHOLDER_RE.findall(s or ""))
