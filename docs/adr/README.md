# Architecture Decision Records

Each ADR records one significant decision: context, decision, consequences and alternatives.
They elaborate the decisions in [`../00_design_baseline.md`](../00_design_baseline.md) §2; if an ADR
and the baseline disagree, the baseline wins. See also the [HLD](../02_hld.md).

| ADR | Title | Status | Baseline ref |
|---|---|---|---|
| [0001](0001-fastapi-for-api.md) | FastAPI for the REST API | Accepted | D1, D2, D13, D14 |
| [0002](0002-pdfplumber-for-extraction.md) | pdfplumber for PDF extraction | Accepted | D3 |
| [0003](0003-template-driven-parsing-with-generic-fallback.md) | Template-driven parsing with a generic fallback | Accepted | D6, D7 |
| [0004](0004-sqlalchemy-sqlite-then-postgres.md) | SQLAlchemy with SQLite for MVP, PostgreSQL in production | Accepted | D4, D5 |
| [0005](0005-http-basic-client-credentials-no-rbac.md) | HTTP Basic client credentials, no RBAC | Accepted | D8 |
| [0006](0006-append-only-hash-chained-audit-log.md) | Append-only, hash-chained audit log | Accepted | D9 |
| [0007](0007-market-detection-before-normalisation.md) | Market detection before normalisation | Accepted | D12 |
| [0008](0008-decimal-strings-for-money.md) | Decimal everywhere, decimal strings in outputs | Accepted | D10, D11 |

**New ADR:** copy the section layout (Title, Status, Context, Decision, Consequences, Alternatives
considered), take the next number, add a row here. Superseded ADRs stay in place with status
`Superseded by ADR-NNNN`.
