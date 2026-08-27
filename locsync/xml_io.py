"""Parse and emit Android strings.xml preserving plurals/arrays/CDATA/placeholders."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from lxml import etree

PLACEHOLDER_RE = re.compile(r"%(?:\d+\$)?[-#+ 0,(]?\d*(?:\.\d+)?[bBhHsScCdoxXeEfgGaAtTn%]")


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
    if _looks_like_xml_markup(raw):
        # Attempt to parse as fragment; if it fails, fall back to escaping.
        try:
            etree.fromstring(f"<r>{raw}</r>")
            return raw
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
