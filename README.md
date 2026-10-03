# Bonds Deal Slip Parser

A stateless REST API that turns bond deal slip PDFs into one canonical deal and returns it as
**JSON, XML or Excel**.

- **Nothing is stored.** Each PDF is processed in memory and discarded; there is no database.
- **New layouts are onboarded once.** An unknown layout comes back `NEW_TEMPLATE` with every
  key/value it found (with page positions). Map it in a preview loop, approve it, and every later
  slip in that layout comes back `PARSED`.
- **Numbers are checked.** Principal, consideration (incl. stamp duty), accrued interest, discount,
  quantity, repo legs, ISIN check digit and dates are validated on every parse.
- **Secured and audited.** HTTP Basic client credentials; an append-only, hash-chained audit log
  that holds metadata only.

Supported today: Indian market slips (G-Sec / SDL / T-Bill / corporate bonds / NCDs / MLDs / repo),
including two-column tables, merged-cell confirmation letters, grids, repo legs, text lines and
letters. Other markets are detected and sent to review.

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate                      # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt             # add requirements-dev.txt for tests and linters
python -m app.cli add-client frontend       # prints the client secret ONCE - store it
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/docs**, click **Authorize**, enter `frontend` and the secret, then use
`POST /api/v1/parse` → *Try it out* with any PDF from `samples/mock/`.

Or run the whole flow from the command line:

```bash
python tools/demo.py                         # in-process demo with the mock slips, no server needed
```

### Docker

```bash
docker compose up --build                    # API on http://127.0.0.1:8000
docker compose exec api python -m app.cli add-client frontend
```

Templates are kept in `./templates` and clients / audit in `./data` (both mounted as volumes).

## Using the API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness (open) |
| `POST /api/v1/parse?format=json\|xml\|xlsx` | Parse a PDF with the approved templates |
| `POST /api/v1/templates/preview` | PDF + draft mapping → the result it would give (nothing saved) |
| `POST /api/v1/templates` | PDF + mapping → save a template (or a new version) |
| `GET / PUT / PATCH /api/v1/templates/{id}`, `POST /api/v1/templates/import` | Manage templates |
| `GET /api/v1/schema/fields`, `/schema/markets` | Canonical fields and markets (for mapping UIs) |
| `GET /api/v1/templates/{id}/history` | Template versions with what changed |
| `GET /api/v1/audit`, `/audit/verify`, `/audit/export` | Search, verify and download the audit log |
| `GET /metrics` | Prometheus metrics (credentials required) |

```bash
# parse (JSON body); use format=xlsx or format=xml to download a file
curl -u frontend:$SECRET -F "file=@samples/mock/01_gsec_outright_purchase.pdf" \
     "http://127.0.0.1:8000/api/v1/parse?format=json"

# onboard a new layout: preview until it is right, then approve
curl -u frontend:$SECRET -F "file=@slip.pdf" \
     -F 'mapping={"label_map": {"Contract Dt": "trade_date"}, "template_name": "Broker XYZ note"}' \
     http://127.0.0.1:8000/api/v1/templates/preview
curl -u frontend:$SECRET -F "file=@slip.pdf" \
     -F 'mapping={"label_map": {"Contract Dt": "trade_date"}, "template_name": "Broker XYZ note"}' \
     http://127.0.0.1:8000/api/v1/templates
```

## Command line

```bash
python -m app.cli debug-extract slip.pdf     # what the parser reads from a PDF, and the field each label maps to
python -m app.cli add-client NAME            # list-clients, disable-client NAME, rotate-secret NAME
python -m app.cli verify-audit               # check the audit hash chain
```

## Configuration

Environment variables (`BONDS_*`), all optional; see [`.env.example`](.env.example). The main ones:
`BONDS_TEMPLATES_DIR` (default `./templates`), `BONDS_DATA_DIR` (clients and audit, default
`./data`), `BONDS_MAX_UPLOAD_MB`, `BONDS_CORS_ORIGINS`, `BONDS_DOCS_ENABLED`.

## Development

```bash
pip install -r requirements-dev.txt
pre-commit install
pytest                                       # unit, golden-file, API, auth and audit tests
ruff check . && ruff format --check . && mypy
python tools/generate_mock_slips.py          # regenerate the fictitious mock slips
python tools/benchmark.py                    # parse-time p50 / p95 against the 3 s target
```

Real slips belong in `samples/real/` (git-ignored, and blocked by a pre-commit hook). Never
commit real client data.

## Documentation

Start with [`docs/README.md`](docs/README.md). The design baseline
[`docs/00_design_baseline.md`](docs/00_design_baseline.md) is the single source of truth; the
stateless decision is [ADR-0009](docs/adr/0009-stateless-api-no-slip-storage.md).
