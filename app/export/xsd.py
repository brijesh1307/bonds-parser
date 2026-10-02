"""XML Schema (XSD) for the XML output, generated from the response models so it can never drift
(docs/05_data_dictionary_and_outputs.md §6, xml_export.py).

Mapping rules, matching `xml_export.to_xml`:
- every model field is an element, in model order (`yield` by its alias);
- a null value is an empty element with `null="true"`;
- lists are a container whose entries are `<item>` elements;
- `fields` holds one element per canonical field path that has a value (any order);
- decimals are `xs:decimal`, dates `xs:date`, enums are enumerations.
"""

from __future__ import annotations

import datetime as dt
import decimal
import enum
import types
import xml.etree.ElementTree as ET
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from app.engine.fields import FIELD_SPECS
from app.export.xml_export import SCHEMA_VERSION
from app.schemas.slip import FieldInfo, ParseResult

XS = "http://www.w3.org/2001/XMLSchema"
ET.register_namespace("xs", XS)
_SCALARS: dict[Any, str] = {
    bool: "xs:boolean",
    int: "xs:integer",
    float: "xs:decimal",
    decimal.Decimal: "xs:decimal",
    dt.date: "xs:date",
    dt.datetime: "xs:dateTime",
    str: "xs:string",
}


def _q(tag: str) -> str:
    return f"{{{XS}}}{tag}"


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    """(base type, nullable) with Optional / Annotated removed."""
    if get_origin(annotation) is Annotated:
        return _unwrap(get_args(annotation)[0])
    if get_origin(annotation) in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        nullable = len(args) < len(get_args(annotation))
        if len(args) == 1:
            base, inner_null = _unwrap(args[0])
            return base, nullable or inner_null
        return tuple(_unwrap(a)[0] for a in args), nullable  # a union of scalars
    return annotation, False


class _Builder:
    def __init__(self) -> None:
        self.root = ET.Element(_q("schema"), {"elementFormDefault": "unqualified"})
        self.defined: set[str] = set()
        self._empty()

    def _empty(self) -> None:
        st = ET.SubElement(self.root, _q("simpleType"), {"name": "Empty"})
        ET.SubElement(
            ET.SubElement(st, _q("restriction"), {"base": "xs:string"}), _q("length"), {"value": "0"}
        )
        self.defined.add("Empty")

    # ------------------------------------------------------------------ simple types

    def _enum(self, name: str, values: list[str]) -> str:
        if name not in self.defined:
            st = ET.SubElement(self.root, _q("simpleType"), {"name": name})
            r = ET.SubElement(st, _q("restriction"), {"base": "xs:string"})
            for v in values:
                ET.SubElement(r, _q("enumeration"), {"value": v})
            self.defined.add(name)
        return name

    def _scalar(self, base: Any, context: str) -> str | None:
        if isinstance(base, tuple):  # union of scalars (FieldInfo.value)
            return "xs:string"
        if base in _SCALARS:
            return _SCALARS[base]
        if isinstance(base, type) and issubclass(base, enum.Enum):
            return self._enum(base.__name__, [str(m.value) for m in base])
        if get_origin(base) is Literal:
            return self._enum(f"{context}Value", [str(v) for v in get_args(base)])
        return None

    def _nullable_simple(self, type_name: str) -> str:
        local = type_name.replace("xs:", "")
        union_name, name = f"U_{local}", f"N_{local}"
        if name not in self.defined:
            st = ET.SubElement(self.root, _q("simpleType"), {"name": union_name})
            ET.SubElement(st, _q("union"), {"memberTypes": f"{type_name} Empty"})
            ct = ET.SubElement(self.root, _q("complexType"), {"name": name})
            ext = ET.SubElement(ET.SubElement(ct, _q("simpleContent")), _q("extension"), {"base": union_name})
            ET.SubElement(ext, _q("attribute"), {"name": "null", "type": "xs:boolean", "use": "optional"})
            self.defined.add(name)
        return name

    # ------------------------------------------------------------------ complex types

    def _complex(
        self,
        name: str,
        children: list[tuple[str, str, int, str]],
        *,
        nullable: bool,
        choice: bool = False,
        attrs: list[ET.Element] | None = None,
    ) -> str:
        """children = [(element name, type, minOccurs, maxOccurs)]."""
        full = f"N_{name}" if nullable else name
        if full in self.defined:
            return full
        self.defined.add(full)
        ct = ET.SubElement(self.root, _q("complexType"), {"name": full})
        group_attrs = {"minOccurs": "0", "maxOccurs": "unbounded"} if choice else {}
        if nullable and not choice:
            group_attrs["minOccurs"] = "0"
        group = ET.SubElement(ct, _q("choice" if choice else "sequence"), group_attrs)
        for el_name, el_type, min_occurs, max_occurs in children:
            a = {"name": el_name, "type": el_type}
            if min_occurs != 1:
                a["minOccurs"] = str(min_occurs)
            if max_occurs != "1":
                a["maxOccurs"] = max_occurs
            ET.SubElement(group, _q("element"), a)
        if nullable:
            ET.SubElement(ct, _q("attribute"), {"name": "null", "type": "xs:boolean", "use": "optional"})
        for attr in attrs or []:
            ct.append(attr)
        return full

    def type_of(self, annotation: Any, context: str) -> str:
        base, nullable = _unwrap(annotation)
        if (scalar := self._scalar(base, context)) is not None:
            return self._nullable_simple(scalar) if nullable else scalar
        if get_origin(base) is list:
            item_type = self.type_of(get_args(base)[0], context)
            name = f"List_{item_type.replace('xs:', '')}"
            return self._complex(name, [("item", item_type, 0, "unbounded")], nullable=nullable)
        if get_origin(base) is dict and _unwrap(get_args(base)[1])[0] is FieldInfo:
            info = self.model(FieldInfo, nullable=False)
            paths = [p for p, s in FIELD_SPECS.items() if not s.internal]
            return self._complex("FieldsMap", [(p, info, 0, "1") for p in paths], nullable=False, choice=True)
        if isinstance(base, type) and issubclass(base, BaseModel):
            return self.model(base, nullable=nullable)
        raise TypeError(f"no XSD mapping for {annotation!r} ({context})")

    def model(
        self,
        model: type[BaseModel],
        *,
        nullable: bool,
        attrs: list[ET.Element] | None = None,
        name: str | None = None,
    ) -> str:
        children = [
            (
                field.alias or field_name,
                self.type_of(field.annotation, f"{model.__name__}_{field_name}"),
                1,
                "1",
            )
            for field_name, field in model.model_fields.items()
        ]
        return self._complex(name or model.__name__, children, nullable=nullable, attrs=attrs)


def build_xsd() -> str:
    b = _Builder()
    version = ET.Element(
        _q("attribute"),
        {"name": "schema_version", "type": "xs:string", "fixed": SCHEMA_VERSION, "use": "required"},
    )
    root_type = b.model(ParseResult, nullable=False, attrs=[version], name="ParseResultDocument")
    ET.SubElement(b.root, _q("element"), {"name": "parse_result", "type": root_type})
    ET.indent(b.root)
    body = ET.tostring(b.root, encoding="unicode")
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- Bonds Deal Slip Parser: XML output of /api/v1/parse?format=xml "
        f"(schema_version {SCHEMA_VERSION}).\n"
        "     Generated from the response models by app/export/xsd.py - do not edit by hand. -->\n"
    )
    return header + body + "\n"
