"""XML output (docs/05_data_dictionary_and_outputs.md §6).

Same content as the JSON response. Rules: root <parse_result schema_version="1">; nulls are
empty elements with null="true"; list entries are <item>; keys that are not valid XML names
are sanitised; decimals stay plain strings.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from pydantic import BaseModel

SCHEMA_VERSION = "1"


def sanitize_tag(key: str) -> str:
    tag = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return tag if re.match(r"[A-Za-z_]", tag) else f"_{tag}"


def _add(parent: ET.Element, key: str, value: Any) -> None:
    el = ET.SubElement(parent, sanitize_tag(key))
    if isinstance(value, dict):
        for k, v in value.items():
            _add(el, k, v)
    elif isinstance(value, list):
        for item in value:
            _add(el, "item", item)
    elif value is None:
        el.set("null", "true")
    elif isinstance(value, bool):
        el.text = "true" if value else "false"
    else:
        el.text = str(value)


def to_xml(result: BaseModel) -> bytes:
    root = ET.Element("parse_result", {"schema_version": SCHEMA_VERSION})
    for key, value in result.model_dump(mode="json", by_alias=True).items():
        _add(root, key, value)
    ET.indent(root)
    body: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body + b"\n"
