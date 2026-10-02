"""The XML output validates against the published XSD, and the XSD is current."""

from __future__ import annotations

from pathlib import Path

import pytest
import xmlschema

from app.config import Settings
from app.engine.pipeline import run
from app.export.xml_export import to_xml
from app.export.xsd import build_xsd
from app.services.parse_service import to_schema
from tests.conftest import MOCK_DIR, ROOT

XSD_FILE = ROOT / "docs" / "schemas" / "parse_result.xsd"


@pytest.fixture(scope="module")
def schema() -> xmlschema.XMLSchema:
    return xmlschema.XMLSchema(build_xsd())


def _xml(pdf: Path) -> str:
    result = run(pdf.read_bytes(), file_name=pdf.name, sha256="0" * 64, es=Settings().engine)
    return to_xml(to_schema(result)).decode("utf-8")


def test_committed_xsd_is_current() -> None:
    assert XSD_FILE.read_text(encoding="utf-8") == build_xsd(), "run: python tools/build_xsd.py"


@pytest.mark.parametrize("pdf", sorted(MOCK_DIR.glob("*.pdf")), ids=lambda p: p.stem)
def test_every_mock_output_is_valid(schema: xmlschema.XMLSchema, pdf: Path) -> None:
    schema.validate(_xml(pdf))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("<price>101.2350</price>", "<price>abc</price>"),  # not a decimal
        ("<status>NEW_TEMPLATE</status>", "<status>MAYBE</status>"),  # not an enum value
        ("<trade_date>2026-09-24</trade_date>", "<trade_date>24/09/2026</trade_date>"),  # not an ISO date
        ('schema_version="1"', 'schema_version="2"'),  # fixed version
    ],
)
def test_schema_rejects_bad_xml(schema: xmlschema.XMLSchema, old: str, new: str) -> None:
    doc = _xml(MOCK_DIR / "01_gsec_outright_purchase.pdf")
    assert old in doc
    assert not schema.is_valid(doc.replace(old, new, 1))
