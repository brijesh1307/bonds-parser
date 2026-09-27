# 03 · Low-Level Design (LLD)

Scope: module-by-module design of the Bonds Deal Slip Parser, the engine algorithms, the slip state
machine, sequence flows, the physical database design, error handling and runtime concerns.

`docs/00_design_baseline.md` is authoritative. Names, values, thresholds, enums, table columns,
audit actions and endpoints below are taken from it verbatim. Anything the baseline does not
decide is marked **[A]** (assumption) and collected in §10.

---

## 0. Conventions

| Topic | Convention |
|---|---|
| Coordinates | PDF points (1/72 in), pdfplumber orientation: origin **top-left**, `bbox = [x0, top, x1, bottom]`, rounded to 0.1. Pages are **1-based**. A4 = `[595.3, 841.9]`. |
| Money / rates | `decimal.Decimal` end to end (D10). No `float` for any amount, price, yield or rate. Confidence and match scores are plain floats rounded to 2 / 4 dp (they are scores, not money) **[A]**. |
| Dates / times | `datetime.date` for business dates, ISO `YYYY-MM-DD` on output; timestamps are timezone-aware UTC, serialised `YYYY-MM-DDTHH:MM:SS.ffffffZ` (D11). |
| Field paths | Canonical field names; nested repo fields are addressed as `repo.<name>` (e.g. `repo.leg2_amount`) in `fields`, `missing_required`, `low_confidence`, label maps and rules. |
| Layering | `api → services → engine / export / db`. `engine` and `export` are pure (no FastAPI, no DB, no I/O except reading the PDF path they are given). `db` knows nothing about FastAPI. |
| Python | 3.11+, `from __future__ import annotations`, full type hints, `StrEnum` for enums. |
| Naming clash | Pydantic has `pydantic.fields.FieldInfo`; our API model is `app.schemas.slip.FieldInfo`. Never import both into the same module under the same name. Engine and schema both define `ParseResult`; import the engine one as `EngineResult`. |

---

## 1. Module map

```
app/
├── main.py                create_app(), middleware, exception handlers, router wiring
├── config.py              Settings (BONDS_* env vars), get_settings()
├── auth.py                HTTP Basic verification, Argon2, FailureLimiter
├── cli.py                 init-db, add-client, list-clients, disable-client, rotate-secret
├── errors.py         [A]  AppError hierarchy (shared by engine, services, api)
├── api/
│   ├── deps.py            get_db, require_client, get_request_context, pagination
│   ├── health.py  parse.py  slips.py  templates.py  schema.py  exports.py  audit.py
├── schemas/
│   ├── common.py          Problem, Page[T], enums re-exported for OpenAPI
│   ├── deal.py            Identifiers, Repo, Deal
│   ├── slip.py            KeyValue, FieldInfo, ValidationIssue, TemplateMatch, MarketInfo,
│   │                      SourceInfo, ParseResult, SlipOut, SlipListItem, BatchItem
│   ├── template.py        RegionRule, RegexRule, MappingSpec, ApproveRequest,
│   │                      TemplateDefinitionModel, TemplateOut, TemplateVersionOut, TemplatePatch
│   └── audit.py           AuditEventOut, AuditVerifyOut
├── services/
│   ├── slip_service.py  template_service.py  export_service.py  audit_service.py
├── engine/
│   ├── fields.py          canonical field registry, synonyms, abbreviations, required rules
│   ├── extract.py         pdfplumber → ExtractedDocument (KeyValue candidates)
│   ├── market.py          market vote → MarketDetection
│   ├── profiles/          __init__.py (registry + JSON loader), IN.json (US/GB/INTL.json later, 09 §4)
│   ├── detect.py          fingerprint + Jaccard template matching
│   ├── mapping.py         label → field candidates, template rules, resolution
│   ├── normalize.py       amounts, dates, rates, ints, ISIN, enums (per market profile)
│   ├── derive.py          derivation rules
│   ├── validate.py        validation checks, required / low-confidence lists
│   └── pipeline.py        run(): orchestrates steps 2–10, decide_status()
├── export/
│   ├── json_export.py  xml_export.py  excel_export.py
└── db/
    ├── database.py        engine, sessions, SQLite pragmas, triggers, init_db()
    └── models.py          Client, Slip, Template, TemplateVersion, AuditLog
```

---

## 2. Shared enums and engine data structures

### 2.1 Enums (`app/engine/fields.py`, re-exported by `app/schemas/common.py`)

```python
class SlipStatus(StrEnum):
    PARSED = "PARSED"; NEEDS_REVIEW = "NEEDS_REVIEW"; NEW_TEMPLATE = "NEW_TEMPLATE"
    APPROVED = "APPROVED"; UNREADABLE = "UNREADABLE"; FAILED = "FAILED"

class DealType(StrEnum):       OUTRIGHT, PRIMARY_AUCTION, PRIMARY_PLACEMENT, REPO, REVERSE_REPO,
                               TREPS_BORROW, TREPS_LEND, LAF
class InstrumentType(StrEnum): GSEC, SDL, TBILL, CMB, CORPORATE_BOND, PSU_BOND, CP, CD,
                               UST, GILT, BUND, JGB, EUROBOND
class BuySell(StrEnum):        BUY, SELL
class DayCount(StrEnum):       "30/360", "30E/360", "ACT/ACT", "ACT/365", "ACT/364", "ACT/360"
class BidType(StrEnum):        COMPETITIVE, NON_COMPETITIVE
class Market(StrEnum):         IN, US, GB, DE, JP, INTL, UNKNOWN
class Method(StrEnum):         template, region, constant, regex, synonym, abbreviation,
                               derived, fuzzy, text
class Source(StrEnum):         table, grid, text, prose
class Severity(StrEnum):       ERROR, WARNING
class ActorType(StrEnum):      client, system, cli
class Outcome(StrEnum):        SUCCESS, FAILURE
class ExportFormat(StrEnum):   json, xml, xlsx
class AuditExportFormat(StrEnum): csv, xlsx, json

METHOD_SCORE: dict[Method, float] = {
    "template": 1.00, "region": 1.00, "constant": 1.00, "regex": 0.95, "synonym": 0.95,
    "abbreviation": 0.90, "derived": 0.90, "text": 0.80,   # fuzzy = ratio × 0.85 (≤ 0.85)
}
ACCEPTED_DERIVED_SCORE = 0.95
```

### 2.2 Engine dataclasses

All engine dataclasses are `@dataclass(slots=True)`; the ones passed between steps are frozen
where noted.

```python
BBox = tuple[float, float, float, float]          # x0, top, x1, bottom (points, top-left origin)

@dataclass(frozen=True, slots=True)
class KeyValue:                                   # one extracted label/value candidate
    key: str                                      # label exactly as printed ("Face Value (INR)")
    value: str                                    # value text, whitespace collapsed
    source: Source                                # table | grid | text | prose
    page: int                                     # 1-based
    key_bbox: BBox | None                         # None for prose candidates
    value_bbox: BBox | None

@dataclass(slots=True)
class ExtractedDocument:
    pairs: list[KeyValue]                         # in reading order (page, top, x0)
    text: str                                     # full text layer, pages joined with "\f"
    pages: int
    page_sizes: list[tuple[float, float]]         # (width, height) per page
    words: list[list[dict]]                       # pdfplumber words per page (for region rules)
    table_bboxes: list[list[BBox]]                # per page; used to exclude words from text pairing
    has_text_layer: bool                          # False → UNREADABLE

@dataclass(slots=True)
class FieldValue:                                 # one resolved canonical field
    value: Any                                    # normalised: str | Decimal | int | date | None
    raw: str | None                               # text the value came from
    method: Method
    confidence: float                             # 0..1, 2 dp
    label: str | None                             # source label (or rule id / "derived:<rule>")
    page: int | None
    bbox: BBox | None                             # value bbox on the page

@dataclass(slots=True)
class Candidate:                                  # pre-resolution: a possible value for a field
    field: str
    kv: KeyValue | None                           # None for constants / derived
    raw: str
    method: Method
    confidence: float
    rank: int                                     # lower wins, see §4.8

@dataclass(frozen=True, slots=True)
class RegionRule:  field: str; page: int; bbox: BBox
@dataclass(frozen=True, slots=True)
class RegexRule:   field: str; pattern: str; group: int = 1; flags: str = "i"   # [A] group/flags

@dataclass(frozen=True, slots=True)
class TemplateDefinition:                         # = template_versions.definition_json
    template_id: str
    version: int
    market: str
    fingerprint_labels: frozenset[str]            # normalised full labels
    keywords: tuple[str, ...]
    match_threshold: float                        # default BONDS_TEMPLATE_MATCH_THRESHOLD
    label_map: Mapping[str, str]                  # normalised label → field path | "_ignore"
    region_rules: tuple[RegionRule, ...]
    regex_rules: tuple[RegexRule, ...]
    constants: Mapping[str, str]
    ignore: frozenset[str]
    accepted_derived: frozenset[str]
    name: str | None = None                       # from templates.name (not in definition_json)
    @classmethod
    def from_json(cls, d: dict, *, name: str | None = None) -> "TemplateDefinition": ...
    def to_json(self) -> dict: ...                # exactly the baseline §6 JSON shape

@dataclass(frozen=True, slots=True)
class MarketProfile:
    code: str                                     # "IN"
    locale: str                                   # "en-IN"
    currency: str                                 # "INR"
    date_order: Literal["DMY", "MDY", "YMD"]      # IN: DMY
    decimal_sep: str; group_sep: str              # IN: ".", ","
    amount_units: Mapping[str, int]               # {"cr": 10**7, "crore": 10**7, "lakh": 10**5, ...}
    weekend: frozenset[int]                       # {5, 6} (Sat, Sun)
    holidays: frozenset[date]                     # settlement holidays
    default_day_count: Mapping[str, str]          # instrument_type → day_count
    default_coupon_frequency: Mapping[str, int]   # GSEC/SDL → 2, CORPORATE_BOND → 1
    platforms: Mapping[str, str]                  # keyword regex → canonical platform
    settlement_systems: tuple[str, ...]           # CCIL, NSDL, CDSL, NCL, ICCL, RBI
    instrument_patterns: tuple[tuple[str, str], ...]   # (regex, instrument_type), first match wins
    tbill_basis: int = 364

@dataclass(frozen=True, slots=True)
class MarketSignal:  signal: str; value: str; market: str; weight: int

@dataclass(slots=True)
class MarketDetection:
    market: str                                   # Market enum value
    issuer_country: str | None
    slip_locale: str | None
    market_confidence: float
    signals: list[MarketSignal]
    profile: MarketProfile | None

@dataclass(slots=True)
class TemplateMatchResult:
    template: TemplateDefinition | None
    score: float                                  # best Jaccard, 4 dp (0.0 if none)
    threshold: float
    matched: bool
    is_draft: bool = False

@dataclass(slots=True)
class Issue:                                      # engine-side ValidationIssue
    code: str; severity: Severity; message: str
    fields: tuple[str, ...] = ()
    expected: str | None = None; actual: str | None = None; tolerance: str | None = None

@dataclass(slots=True)
class ParseResult:                                # engine output (EngineResult)
    status: SlipStatus
    file_name: str; pages: int; page_sizes: list[tuple[float, float]]; sha256: str
    market: MarketDetection
    template: TemplateMatchResult
    deal: dict[str, Any]                          # canonical dict incl. identifiers / repo
    fields: dict[str, FieldValue]                 # path → FieldValue (only fields with a value)
    key_values: list[KeyValue]
    mapped_to: dict[int, str]                     # index in key_values → field path
    missing_required: list[str]
    validation: list[Issue]
    low_confidence: list[str]
    unmapped: list[KeyValue]                      # not mapped and not ignored
```

---

## 3. Module design

### 3.1 `app/main.py`

| Item | Design |
|---|---|
| `create_app(settings: Settings \| None = None) -> FastAPI` | Title "Bonds Deal Slip Parser", version from package, `openapi_tags` (System, Parse, Slips, Templates, Schema, Exports, Audit). `docs_url="/docs"`, `redoc_url="/redoc"`, `openapi_url="/openapi.json"`; all three `None` when `BONDS_DOCS_ENABLED=false`. OpenAPI security scheme `HTTPBasic` so Swagger's *Authorize* button works. |
| Middleware (outer → inner) | `RequestIdMiddleware` (reads/validates `X-Request-ID` ≤ 128 chars `[A-Za-z0-9._-]`, else `uuid4().hex`; stores on `request.state.request_id`; echoes header on every response incl. errors), `AccessLogMiddleware` (one log line per request), `CORSMiddleware` (origins = `BONDS_CORS_ORIGINS`, methods GET/POST/PUT/PATCH/DELETE, headers `Authorization, Content-Type, X-Actor-Name, X-Request-ID`, expose `X-Request-ID, Content-Disposition`, `allow_credentials=False`). |
| Exception handlers | `AppError`, `RequestValidationError`, `StarletteHTTPException`, `sqlalchemy.exc.OperationalError`, `Exception` → RFC 7807 (§8). |
| Routers | `health` at root; all others with `prefix="/api/v1"`. |
| Lifespan | `settings.data_dir` and `uploads_dir` created; `init_db(engine)` (MVP: `create_all` + triggers; Alembic after MVP); `FailureLimiter` singleton on `app.state`. |

### 3.2 `app/config.py`

```python
@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = Path("./data")                              # BONDS_DATA_DIR
    database_url: str = "sqlite:///./data/bonds.db"              # BONDS_DATABASE_URL
    max_upload_mb: int = 10                                      # BONDS_MAX_UPLOAD_MB
    max_pages: int = 20                                          # BONDS_MAX_PAGES
    confidence_threshold: float = 0.90                           # BONDS_CONFIDENCE_THRESHOLD
    template_match_threshold: float = 0.80                       # BONDS_TEMPLATE_MATCH_THRESHOLD
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://localhost:5173")
    docs_enabled: bool = True                                    # BONDS_DOCS_ENABLED
    auth_max_failures: int = 10                                  # BONDS_AUTH_MAX_FAILURES
    auth_window_seconds: int = 300                               # BONDS_AUTH_WINDOW_SECONDS
    auth_block_seconds: int = 900                                # BONDS_AUTH_BLOCK_SECONDS
    log_level: str = "INFO"                                      # BONDS_LOG_LEVEL
    audit_retention_years: int = 8                               # BONDS_AUDIT_RETENTION_YEARS

    @property
    def uploads_dir(self) -> Path: return self.data_dir / "uploads"
    @property
    def max_upload_bytes(self) -> int: return self.max_upload_mb * 1024 * 1024

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings": ...

@lru_cache
def get_settings() -> Settings: return Settings.from_env()
```

- Parsing: ints via `int()`, bools accept `true/false/1/0/yes/no` (case-insensitive), CORS split on
  `,` and stripped, thresholds must be in `[0, 1]`, `log_level` in the stdlib level names.
- Invalid value → `ConfigError` (subclass of `ValueError`) at startup; the process refuses to start.
- No `pydantic-settings` dependency (not in the baseline library list) **[A]**.
- `EngineSettings` (a slice passed to the engine so it stays config-agnostic):
  `EngineSettings(confidence_threshold: float, template_match_threshold: float, max_pages: int)`.

### 3.3 `app/auth.py`

```python
_hasher = argon2.PasswordHasher()                 # argon2-cffi defaults (Argon2id)
DUMMY_HASH: str = _hasher.hash("dummy-secret-for-timing")
CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,63}$")   # [A] no ':' (Basic separator)

@dataclass(frozen=True, slots=True)
class ClientPrincipal:  client_id: str; description: str | None

def generate_secret() -> str                      # secrets.token_urlsafe(32) (43 chars)
def hash_secret(secret: str) -> str
def verify_secret(secret_hash: str, secret: str) -> bool     # False on VerifyMismatchError / InvalidHash
def parse_basic_header(value: str | None) -> tuple[str, str] # raises AuthError("malformed")

def authenticate(db: Session, header: str | None, ip: str, ctx: RequestContext,
                 limiter: FailureLimiter) -> ClientPrincipal

class FailureLimiter:                              # in-process, per IP (after MVP; no-op in MVP mode)
    def __init__(self, max_failures: int, window_s: int, block_s: int, clock=time.monotonic): ...
    def check(self, ip: str) -> None               # raises AuthBlockedError(retry_after) if blocked
    def record_failure(self, ip: str) -> bool      # True if this failure triggered a new block
    def reset(self, ip: str) -> None               # on successful auth
```

`authenticate` logic:

```
limiter.check(ip)                                           → 429 if blocked
client_id, secret = parse_basic_header(header)              → reason "malformed"
row = SELECT * FROM clients WHERE client_id = :client_id
hash = row.secret_hash if row else DUMMY_HASH               # constant-time-ish: always run Argon2
ok = verify_secret(hash, secret) and row is not None and row.is_active
if not ok:
    reason = "unknown_client" | "bad_secret" | "inactive" | "malformed"
    audit_service.record_standalone(action=AUTH_FAILED, outcome=FAILURE, actor_id=client_id[:64],
                                    details={"reason": reason}, ctx)
    if limiter.record_failure(ip): audit_service.record_standalone(action=AUTH_BLOCKED, ...)
    raise AuthError()                                       → 401 + WWW-Authenticate: Basic realm="bonds-parser"
if _hasher.check_needs_rehash(row.secret_hash): row.secret_hash = hash_secret(secret)
row.last_used_at = now_utc()            (committed with the request's first write; throttled to 1/min [A])
limiter.reset(ip)
return ClientPrincipal(row.client_id, row.description)
```

The response never reveals which reason applied. Secrets and the `Authorization` header are never
logged. `FailureLimiter` state is per process (multi-worker deployments need Redis — after MVP).

### 3.4 `app/cli.py`

`python -m app.cli <command>`; `argparse`; runs with `actor_type="cli"`, `actor_id=getpass.getuser()`.

| Command | Arguments | Effect | Audit action | Exit codes |
|---|---|---|---|---|
| `init-db` | – | `init_db(engine)`: tables, indexes, triggers; idempotent | – | 0 |
| `add-client` | `client_id`, `--description` | validates `CLIENT_ID_RE`, generates secret, stores Argon2 hash, **prints secret once** | `CLIENT_ADDED` | 0 / 1 exists / 2 invalid |
| `list-clients` | – | table: client_id, active, created_at, last_used_at, rotated_at (never hashes) | – | 0 |
| `disable-client` | `client_id` | `is_active = false` | `CLIENT_DISABLED` | 0 / 1 not found |
| `rotate-secret` | `client_id` | new secret, new hash, `rotated_at = now`; prints secret once | `CLIENT_SECRET_ROTATED` | 0 / 1 not found |

`def main(argv: Sequence[str] | None = None) -> int`.

### 3.5 `app/api/deps.py`

```python
@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    actor_type: ActorType              # "client" for API calls
    actor_id: str | None               # client_id (None before auth)
    actor_name: str | None             # X-Actor-Name: control chars stripped, max 200 chars
    ip_address: str | None             # request.client.host (proxy headers after MVP)
    user_agent: str | None             # truncated to 512

def get_db() -> Iterator[Session]                           # one Session per request; closed in finally
def require_client(request: Request, db: Session = Depends(get_db)) -> ClientPrincipal
def get_request_context(request: Request,
                        client: ClientPrincipal = Depends(require_client)) -> RequestContext
def pagination(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> PageParams
def get_engine_settings(settings: Settings = Depends(get_settings)) -> EngineSettings
```

Every business router declares `dependencies=[Depends(require_client)]` so an unauthenticated call
never reaches validation of the body (auth is checked first).

### 3.6 Routers (`app/api/*.py`)

All handlers are plain `def` (run in the threadpool: pdfplumber and SQLAlchemy are synchronous).

| Module | Handler | Method / path | Request | Response (success) | Audit |
|---|---|---|---|---|---|
| health | `health` | GET `/health` | – | 200 `HealthOut{status, db, version, time}`; 503 if `SELECT 1` fails | – |
| parse | `parse_once` | POST `/api/v1/parse?format=` | multipart `file` | 200 body in `format` (JSON = `ParseResult`) | `SLIP_PARSED` (entity_type `document`, entity_id = sha256) **[A]** |
| slips | `upload_slip` | POST `/api/v1/slips` | multipart `file` | 201 `SlipOut` (`duplicate: true`, `duplicate_of` set on sha256 hit) | `SLIP_UPLOADED`, `SLIP_PARSED` |
| | `upload_batch` | POST `/api/v1/slips/batch` | multipart `files[]` | 200 `list[BatchItem]` (per-file result or problem) | per file as above |
| | `list_slips` | GET `/api/v1/slips` | `status, market, template_id, limit, offset` | 200 `Page[SlipListItem]` | – |
| | `get_slip` | GET `/api/v1/slips/{id}` | – | 200 `SlipOut` | `SLIP_VIEWED` |
| | `get_slip_pdf` | GET `/api/v1/slips/{id}/pdf` | – | 200 `application/pdf` (`FileResponse`) | `SLIP_PDF_DOWNLOADED` |
| | `preview_slip` | POST `/api/v1/slips/{id}/preview` | `MappingSpec` | 200 `ParseResult` (not saved) | `SLIP_PREVIEWED` |
| | `approve_slip` | POST `/api/v1/slips/{id}/approve` | `ApproveRequest` | 200 `SlipOut` (status `APPROVED`) | `TEMPLATE_CREATED` or `TEMPLATE_VERSION_ADDED`, `SLIP_APPROVED` |
| | `reparse_slip` | POST `/api/v1/slips/{id}/reparse?force=` | – | 200 `SlipOut` | `SLIP_REPARSED` |
| | `export_slip` | GET `/api/v1/slips/{id}/export?format=` | – | 200 file (attachment) | `SLIP_EXPORTED` |
| | `slip_history` | GET `/api/v1/slips/{id}/history` | – | 200 `list[AuditEventOut]` (oldest first) | – |
| | `delete_slip` | DELETE `/api/v1/slips/{id}` | – | 204 | `SLIP_DELETED` |
| templates | `list_templates` | GET `/api/v1/templates` | `is_active, market, limit, offset` | 200 `Page[TemplateOut]` | – |
| | `get_template` | GET `/api/v1/templates/{id}` | – | 200 `TemplateOut` incl. `versions` | – |
| | `put_template` | PUT `/api/v1/templates/{id}` | `TemplateDefinitionModel` + `note` | 201 `TemplateVersionOut` | `TEMPLATE_VERSION_ADDED` |
| | `patch_template` | PATCH `/api/v1/templates/{id}` | `TemplatePatch` | 200 `TemplateOut` | `TEMPLATE_ENABLED` / `TEMPLATE_DISABLED` (rename is recorded in `changes`) |
| | `import_template` | POST `/api/v1/templates/import` | `TemplateImport` | 201 `TemplateOut` | `TEMPLATE_IMPORTED` |
| | `template_history` | GET `/api/v1/templates/{id}/history` | – | 200 `list[TemplateHistoryItem{event, diff}]` | – |
| schema | `get_fields` | GET `/api/v1/schema/fields` | – | 200 `list[FieldDef]` | – |
| | `get_markets` | GET `/api/v1/schema/markets` | – | 200 `list[MarketProfileOut]` | – |
| exports | `export_deals` | GET `/api/v1/exports/deals` | `format, status, from, to` | 200 file | `DEALS_EXPORTED` |
| audit | `search_audit` | GET `/api/v1/audit` | filters + `limit, offset` | 200 `Page[AuditEventOut]` | – |
| | `verify_audit` | GET `/api/v1/audit/verify` | – | 200 `AuditVerifyOut` | – |
| | `export_audit` | GET `/api/v1/audit/export` | `format=csv\|xlsx\|json, from, to` | 200 file | `AUDIT_EXPORTED` |

Media types: JSON `application/json`, XML `application/xml`, Excel
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, CSV `text/csv; charset=utf-8`.
Downloads carry `Content-Disposition: attachment; filename="<name>"` where `<name>` is the
`deal_id` (characters outside `[A-Za-z0-9._-]` → `_`) or the slip id, plus extension.

### 3.7 Schemas (`app/schemas/*`, Pydantic v2)

Decimals use a plain-string serializer so they never render in exponent form:

```python
def to_plain_str(d: Decimal) -> str: return format(d, "f")
PlainDecimal = Annotated[Decimal, PlainSerializer(to_plain_str, return_type=str, when_used="json")]
```

#### `common.py`

| Model | Fields |
|---|---|
| `Problem` | `type: str`, `title: str`, `status: int`, `detail: str \| None`, `instance: str \| None`, `request_id: str`, `errors: list[dict] \| None` (field errors / validation issues) |
| `Page[T]` (generic) | `items: list[T]`, `total: int`, `limit: int`, `offset: int` |
| `HealthOut` | `status: Literal["ok","degraded"]`, `db: Literal["ok","error"]`, `version: str`, `time: datetime` |

#### `deal.py`

| Model | Fields |
|---|---|
| `Identifiers` | `isin, cusip, sedol, common_code: str \| None` |
| `Repo` | `repo_rate: PlainDecimal?`, `repo_days: int?`, `day_count: DayCount?`, `haircut: PlainDecimal?`, `leg1_date: date?`, `leg1_price: PlainDecimal?`, `leg1_accrued_days: int?`, `leg1_accrued_interest: PlainDecimal?`, `leg1_amount: PlainDecimal?`, `leg2_date: date?`, `leg2_price: PlainDecimal?`, `leg2_accrued_days: int?`, `leg2_accrued_interest: PlainDecimal?`, `leg2_amount: PlainDecimal?`, `repo_interest: PlainDecimal?` |
| `Deal` | every canonical field of baseline §5 in that order; all optional (`None` default). Types per `docs/05_data_dictionary_and_outputs.md` §1. `yield_: PlainDecimal \| None = Field(None, alias="yield")` (Python keyword) with `model_config = ConfigDict(populate_by_name=True)` and routes using `response_model_by_alias=True` (default). `identifiers: Identifiers`, `repo: Repo \| None`. |

#### `slip.py`

| Model | Fields |
|---|---|
| `KeyValue` | `key: str`, `value: str`, `source: Source`, `page: int`, `key_bbox: list[float] \| None` (len 4), `value_bbox: list[float] \| None`, `mapped_to: str \| None` |
| `FieldInfo` | `value: str \| int \| date \| None` (decimals as strings), `raw: str \| None`, `method: Method`, `confidence: float`, `label: str \| None`, `page: int \| None`, `bbox: list[float] \| None` |
| `ValidationIssue` | `code: str`, `severity: Severity`, `message: str`, `fields: list[str]`, `expected: str \| None`, `actual: str \| None`, `tolerance: str \| None` |
| `TemplateMatch` | `template_id: str \| None`, `name: str \| None`, `version: int \| None`, `score: float`, `threshold: float`, `matched: bool`, `is_draft: bool` |
| `MarketSignalOut` | `signal: str`, `value: str`, `market: Market`, `weight: int` |
| `MarketInfo` | `market: Market`, `issuer_country: str \| None`, `slip_locale: str \| None`, `market_confidence: float`, `profile_available: bool`, `signals: list[MarketSignalOut]` |
| `SourceInfo` | `file: str`, `pages: int`, `page_sizes: list[list[float]]`, `sha256: str` |
| `ParseResult` | `status: SlipStatus`, `source: SourceInfo`, `market: MarketInfo`, `template: TemplateMatch`, `deal: Deal`, `fields: dict[str, FieldInfo]`, `key_values: list[KeyValue]`, `missing_required: list[str]`, `validation: list[ValidationIssue]`, `low_confidence: list[str]`, `unmapped: list[KeyValue]` |
| `SlipOut` (extends `ParseResult`) | `id: UUID`, `duplicate: bool = False`, `duplicate_of: UUID \| None`, `created_by: str`, `created_at: datetime`, `updated_at: datetime`, `approved_by: str \| None`, `approved_actor_name: str \| None`, `approved_at: datetime \| None` |
| `SlipListItem` | `id`, `file_name`, `status`, `market`, `issuer_country`, `template_id`, `template_version`, `match_score`, `deal_id`, `deal_type`, `isin`, `trade_date`, `consideration: PlainDecimal?`, `duplicate_of`, `created_by`, `created_at`, `updated_at`, `approved_at` |
| `BatchItem` | `file_name: str`, `slip: SlipOut \| None`, `error: Problem \| None` |

`ParseResult` is built from the engine result by `slip_service.to_schema(res: EngineResult) -> ParseResult`.
`UNREADABLE` / `FAILED` results carry an empty `deal` (all null), empty lists and `template.matched=false`.

#### `template.py`

| Model | Fields / constraints |
|---|---|
| `RegionRule` | `field: str` (must be a canonical path), `page: int ≥ 1`, `bbox: list[float]` (len 4, `x0<x1`, `top<bottom`) |
| `RegexRule` | `field: str`, `pattern: str` (must compile; ≤ 500 chars; ≥ 1 group) , `group: int = 1` **[A]** |
| `MappingSpec` | `label_map: dict[str, str] = {}` (raw label → field path or `"_ignore"`), `region_rules: list[RegionRule] = []`, `regex_rules: list[RegexRule] = []`, `constants: dict[str, str] = {}`, `template_id: str \| None`, `template_name: str \| None`, `keywords: list[str] = []`, `accept_validation_issues: bool = False`, `note: str \| None` |
| `ApproveRequest` | subclass of `MappingSpec`; model validator: `template_id` or `template_name` required |
| `TemplateDefinitionModel` | mirrors baseline §6 JSON: `template_id, version, market, fingerprint{labels, keywords}, match_threshold, label_map, region_rules, regex_rules, constants, ignore, accepted_derived` |
| `TemplateImport` | `name: str`, `description: str \| None`, `definition: TemplateDefinitionModel` |
| `TemplatePatch` | `name: str \| None`, `description: str \| None`, `is_active: bool \| None` (at least one) |
| `TemplateVersionOut` | `version: int`, `definition: TemplateDefinitionModel`, `source_slip_id: UUID \| None`, `note: str \| None`, `created_by: str`, `created_at: datetime` |
| `TemplateOut` | `id`, `name`, `description`, `market`, `is_active`, `current_version`, `match_count`, `last_matched_at`, `created_by`, `created_at`, `updated_at`, `definition: TemplateDefinitionModel` (current), `versions: list[TemplateVersionOut] \| None` (detail only) |

`MappingSpec` validation errors (unknown field path, bad regex, bbox outside page) raise
`MappingValidationError` → 422 with one entry per problem in `Problem.errors`. Field paths are
checked against `fields.is_valid_field_path()`; page / bbox against the slip's `page_sizes`.

#### `audit.py`

| Model | Fields |
|---|---|
| `AuditEventOut` | `id, event_id, occurred_at, actor_type, actor_id, actor_name, action, entity_type, entity_id, status_before, status_after, changes, details, request_id, ip_address, user_agent, outcome, error, prev_hash, row_hash` |
| `AuditVerifyOut` | `ok: bool`, `checked: int`, `first_bad_id: int \| None`, `reason: Literal["row_hash_mismatch","prev_hash_mismatch"] \| None`, `last_hash: str \| None` |

Schema endpoint models: `FieldDef{name, type, format, enum, required_when, description, example}`,
`MarketProfileOut{code, locale, currency, date_order, available: bool}`.

### 3.8 Services (`app/services/*`)

Services own transactions: they call `db.commit()` / `db.rollback()`. Routers never commit.

#### `slip_service.py`

```python
def create_slip(db: Session, upload: UploadFile, ctx: RequestContext, es: EngineSettings,
                settings: Settings) -> Slip        # always a new row; duplicate_of set on sha256 hit
def create_batch(db, uploads: list[UploadFile], ctx, es, settings) -> list[BatchItem]
def parse_once(upload: UploadFile, ctx, es, settings, db: Session) -> EngineResult
def list_slips(db, *, status: SlipStatus | None, market: str | None, template_id: str | None,
               limit: int, offset: int) -> tuple[list[Slip], int]
def get_slip(db, slip_id: UUID, ctx, *, audit: bool = True) -> Slip                     # NotFoundError
def get_pdf_path(db, slip_id: UUID, ctx) -> Path                                        # NotFoundError
def preview(db, slip_id: UUID, spec: MappingSpec, ctx, es) -> EngineResult
def approve(db, slip_id: UUID, req: ApproveRequest, ctx, es) -> Slip
def reparse(db, slip_id: UUID, ctx, es, *, force: bool = False) -> Slip
def delete_slip(db, slip_id: UUID, ctx) -> None
def history(db, slip_id: UUID) -> list[AuditLog]
def to_schema(res: EngineResult) -> ParseResult
def to_slip_out(slip: Slip, *, duplicate: bool = False) -> SlipOut
```

Key internal helpers: `_receive_upload(upload, settings) -> StagedFile{tmp_path, sha256, size}`
(stream in 64 KiB chunks, hash while writing, abort at `max_upload_bytes` → `PayloadTooLargeError`,
check `%PDF-` magic → `UnsupportedMediaTypeError`); `_check_pdf(tmp_path, max_pages) -> int`
(open with pdfplumber; encrypted → `EncryptedPdfError`; unreadable → `InvalidPdfError`;
pages > max → `TooManyPagesError`).

Errors: `NotFoundError` (404), `InvalidStateError` (409), `ValidationNotAcceptedError` (409),
`ApprovalBlockedError` (422),
`MappingValidationError` (422), upload errors (413/415/422).

#### `template_service.py`

```python
def load_active_definitions(db: Session) -> list[TemplateDefinition]
def list_templates(db, *, is_active: bool | None, market: str | None, limit, offset) -> tuple[list[Template], int]
def get_template(db, template_id: str) -> Template                               # NotFoundError
def build_definition(spec: MappingSpec, result: EngineResult, *, base: TemplateDefinition | None,
                     template_id: str, version: int, market: str, threshold: float) -> TemplateDefinition
def save_from_approval(db, spec: ApproveRequest, slip: Slip, result: EngineResult,
                       ctx: RequestContext) -> tuple[Template, TemplateVersion, bool]   # created?
def add_version(db, template_id: str, definition: TemplateDefinition, ctx, *,
                source_slip_id: UUID | None = None, note: str | None = None) -> TemplateVersion
def patch_template(db, template_id: str, patch: TemplatePatch, ctx) -> Template
def import_template(db, payload: TemplateImport, ctx) -> Template
def template_history(db, template_id: str) -> list[TemplateHistoryItem]
def diff_definitions(old: dict, new: dict) -> dict        # {"label_map": {"added":…, "removed":…, "changed":…}, …}
def record_match(db, template_id: str) -> None            # UPDATE … SET match_count = match_count + 1, last_matched_at = now
def slugify(name: str) -> str                             # lowercase, [a-z0-9_], max 100; suffix _2, _3 on clash
```

`build_definition` rules:

| Part | Rule |
|---|---|
| `label_map` | `base.label_map` (if a new version) ∪ spec entries with keys normalised (`normalise_label(k).full`); spec wins on clash; `"_ignore"` targets move to `ignore` |
| `fingerprint.labels` | normalised full labels of this slip's `table`/`grid`/`text` key_values (prose excluded) |
| `fingerprint.keywords` | `spec.keywords` (or base keywords if empty) |
| `region_rules`, `regex_rules`, `constants` | spec replaces base when provided (non-empty), else base carried over **[A]** |
| `accepted_derived` | base ∪ required fields of this result whose method is `derived`, `text` or `fuzzy` and which the reviewer did not remap |
| `match_threshold` | base value, else `BONDS_TEMPLATE_MATCH_THRESHOLD` |
| `market` | detected market of the slip (must not be `UNKNOWN` → `ApprovalBlockedError`) **[A]** |

Template versions are immutable; a change always produces `version = current_version + 1`.

#### `export_service.py`

```python
@dataclass(frozen=True, slots=True)
class ExportFile: content: bytes; media_type: str; filename: str

def export_slip(db, slip_id: UUID, fmt: ExportFormat, ctx) -> ExportFile
def export_deals(db, fmt: ExportFormat, *, status: SlipStatus | None, date_from: date | None,
                 date_to: date | None, ctx) -> ExportFile      # filter on slips.created_at (UTC date)
def render(results: Sequence[ParseResult], fmt: ExportFormat, *, single: bool,
           slip_ids: Sequence[str | None]) -> ExportFile
```

#### `audit_service.py`

```python
GENESIS_HASH = "0" * 64

def record(db: Session, *, action: str, ctx: RequestContext | SystemActor, entity_type: str | None,
           entity_id: str | None, status_before: str | None = None, status_after: str | None = None,
           changes: dict | None = None, details: dict | None = None,
           outcome: Outcome = Outcome.SUCCESS, error: str | None = None) -> AuditLog   # flush, no commit
def record_standalone(**kwargs) -> None       # own Session + commit (AUTH_FAILED, AUTH_BLOCKED, FAILED paths)
def canonical_json(obj: Any) -> str
def compute_row_hash(row: Mapping[str, Any], prev_hash: str) -> str
def verify_chain(db: Session, batch_size: int = 1000) -> AuditVerifyOut
def search(db, *, entity_type, entity_id, actor_id, action, date_from, date_to, limit, offset) -> tuple[list[AuditLog], int]
def export(db, fmt: AuditExportFormat, *, date_from, date_to, ctx) -> ExportFile
```

### 3.9 Engine (`app/engine/*`)

#### `fields.py` — canonical field registry

```python
DecimalKind = Literal["nominal", "cash", "price", "yield", "rate", "fx"]

@dataclass(frozen=True, slots=True)
class FieldSpec:
    path: str                                  # "face_value", "repo.leg1_amount"
    type: Literal["str", "enum", "date", "decimal", "int", "float"]
    kind: DecimalKind | None = None            # decimal fields only (drives scale, §4.14 of doc 05)
    enum: tuple[str, ...] | None = None
    required_for: frozenset[str] | Literal["ALL"] | None = None
    description: str = ""

FIELD_SPECS: dict[str, FieldSpec]              # every canonical + repo.* + identifiers.* path
SYNONYMS: dict[str, str]                       # normalised label → field path (starter set of
                                               # deal_slip_standard_formats.md §4 + repo leg keys)
ABBREVIATIONS: dict[str, str]                  # §4.5
LEG_PREFIXES: dict[str, str]                   # "first leg"/"leg 1"/"1st leg" → "repo.leg1_",
                                               # "second leg"/"leg 2"/"2nd leg" → "repo.leg2_"
LEG_SUFFIXES: dict[str, str]                   # "date" → "date", "price" → "price",
                                               # "accrued days"/"accr days" → "accrued_days",
                                               # "accrued interest"/"accrued int" → "accrued_interest",
                                               # "amount"/"settlement amount" → "amount"
BASE_REQUIRED = ("deal_id", "deal_type", "trade_date", "settlement_date",
                 "security_name", "isin", "face_value", "consideration")
REQUIRED_BY_TYPE = {"OUTRIGHT": ("buy_sell", "price"), "PRIMARY_AUCTION": ("price",),
                    "REPO": ("repo.repo_rate", "repo.leg1_amount", "repo.leg2_date", "repo.leg2_amount"),
                    "REVERSE_REPO": ("repo.repo_rate", "repo.leg1_amount", "repo.leg2_date", "repo.leg2_amount")}

def required_fields(deal_type: str | None) -> list[str]
def is_valid_field_path(path: str) -> bool
def empty_deal() -> dict[str, Any]             # all keys, None values, identifiers {…None}, repo None
def field_defs() -> list[FieldDef]             # for GET /schema/fields
```

#### `extract.py`

```python
def extract(pdf: Path | bytes, *, max_pages: int) -> ExtractedDocument
def normalise_label(label: str) -> tuple[str, str]          # (full, short), §4.4
def label_like(s: str) -> bool                              # §4.1
def clean_cell(s: str | None) -> str                        # collapse whitespace; "____"/"----" → ""
def repair_isin_overflow(row: list[str]) -> list[str]       # §4.2
def classify_table(rows: list[list[str]]) -> Literal["KV", "KV2", "GRID", "UNKNOWN"]
```

Internals: `_table_pairs(page, page_no)`, `_grid_pairs(rows, cells, page_no)`,
`_text_line_pairs(page, page_no, exclude: list[BBox])`, `_prose_pairs(text, words)`.
Errors: `InvalidPdfError`, `EncryptedPdfError`, `TooManyPagesError`. A document whose text layer
has fewer than 20 non-whitespace characters sets `has_text_layer=False` **[A]**.

Prose patterns (source `prose`, generic, run on the full text):

| Key emitted | Pattern (case-insensitive) |
|---|---|
| `ISIN` | `\b([A-Z]{2}[A-Z0-9]{9}\d)\b` with valid ISO 6166 check digit |
| `Bid Type` | `\b(non[- ]?competitive\|competitive)\s+bid\b` |
| `Tenor` | `\b(\d{2,3})[- ]day\s+(treasury bill\|t-?bill\|cash management bill)` |
| `Yield` | `at a yield of ([\d.]+)\s*%` |
| `Counterparty` | `conducted by (?:the )?(Reserve Bank of India)` |
| `Dealer` | `\(([A-Z]\.\s?[A-Z][A-Za-z]+)\)\s*\n\s*Dealer\b` |

#### `market.py`

```python
def detect_market(doc: ExtractedDocument) -> MarketDetection     # algorithm §4.9
def isin_country(isin: str) -> str | None                         # "XS"/"EU" → None
```

#### `profiles/`

```python
# profiles/__init__.py — loads app/engine/profiles/<CODE>.json (09 §4) into MarketProfile objects
def get_profile(market: str) -> MarketProfile | None     # MVP: only IN.json has status "active"
def available_profiles() -> list[MarketProfile]
# What profiles/IN.json loads to (content defined in 09 §4.1):
INDIA = MarketProfile(code="IN", locale="en-IN", currency="INR", date_order="DMY",
    decimal_sep=".", group_sep=",",
    amount_units={"cr": 10**7, "crs": 10**7, "crore": 10**7, "crores": 10**7,
                  "lakh": 10**5, "lakhs": 10**5, "lac": 10**5, "lacs": 10**5},
    weekend=frozenset({5, 6}), holidays=load_holidays("in_settlement_holidays.json"),   # [A] data file
    default_day_count={"GSEC": "30/360", "SDL": "30/360", "TBILL": "ACT/364", "CMB": "ACT/364",
                       "CORPORATE_BOND": "ACT/ACT", "PSU_BOND": "ACT/ACT", "CP": "ACT/365", "CD": "ACT/365"},
    default_coupon_frequency={"GSEC": 2, "SDL": 2, "CORPORATE_BOND": 1, "PSU_BOND": 1},
    platforms={r"nds-?om": "NDS-OM", r"croms": "CROMS (CCIL)", r"e-?kuber": "RBI E-Kuber",
               r"\botc\b.*\bnse rfq\b": "OTC / NSE RFQ", r"nse rfq": "NSE RFQ", r"bse rfq": "BSE RFQ",
               r"\bebp\b": "EBP", r"treps": "TREPS (CCIL)", r"\botc\b": "OTC"},
    settlement_systems=("CCIL", "NSDL", "CDSL", "NCL", "NSE Clearing", "ICCL", "RBI"),
    instrument_patterns=((r"\bDTB\b|treasury bill|t-?bill", "TBILL"), (r"\bCMB\b|cash management bill", "CMB"),
                         (r"\bSDL\b|state development loan", "SDL"),
                         (r"\bGS\b\s*\d{4}|government security|g-?sec", "GSEC"),
                         (r"\bCP\b|commercial paper", "CP"), (r"\bCD\b|certificate of deposit", "CD"),
                         (r"\bPSU\b", "PSU_BOND"), (r"\bNCD\b|debenture|bond", "CORPORATE_BOND")))
```

#### `detect.py`

```python
def fingerprint(doc: ExtractedDocument) -> frozenset[str]
def jaccard(a: frozenset[str], b: frozenset[str]) -> float
def match_template(doc: ExtractedDocument, templates: Sequence[TemplateDefinition],
                   default_threshold: float, *, stats: Mapping[str, tuple[int, datetime]] | None = None
                   ) -> TemplateMatchResult                         # algorithm §4.7
```

#### `mapping.py`

```python
def collect_candidates(doc: ExtractedDocument, template: TemplateDefinition | None
                       ) -> tuple[dict[str, list[Candidate]], dict[int, str], list[int]]
    # (candidates per field, kv index → field path, indices of ignored kvs)
def apply_rules(doc: ExtractedDocument, template: TemplateDefinition, cands: dict[str, list[Candidate]]) -> None
def lookup_label(label: str, template: TemplateDefinition | None) -> tuple[str, Method, float] | None
def expand_abbreviations(normalised: str) -> str
def fuzzy_lookup(normalised: str, keys: Iterable[str]) -> tuple[str, float] | None
def resolve(cands: dict[str, list[Candidate]], normalised: dict[int, Any],
            ) -> tuple[dict[str, FieldValue], list[Issue]]           # §4.8
```

Region rule read: `page.crop(bbox).extract_text()` (words fully inside bbox, joined), value
`bbox` = the rule bbox. Regex rule: `re.search(pattern, doc.text, flags)` → `group(n)`; bbox of the
matched words when locatable, else `None`. Errors: none raised; a rule that yields nothing adds a
`RULE_NO_MATCH` WARNING issue.

#### `normalize.py`

```python
def normalize_candidate(path: str, raw: str, profile: MarketProfile | None, doc_hints: DocHints) -> Any
def parse_date(raw: str, profile: MarketProfile | None, hints: DocHints) -> date   # §4.10
def parse_amount(raw: str, profile: MarketProfile | None) -> Decimal               # §4.11
def parse_rate(raw: str) -> Decimal            # "7.10% p.a. (Semi-Annual)" → 7.10 (first number)
def parse_int(raw: str) -> int                 # "167 (30/360)" → 167 (leading integer)
def normalize_isin(raw: str) -> str            # upper, strip spaces / hyphens
def normalize_enum(path: str, raw: str) -> str | None  # direction, day_count, bid_type, deal_type, instrument_type,
                                                       # platform, settlement_mode, broker (None = explicit "no value")
def scale(d: Decimal, kind: DecimalKind, source_scale: int) -> Decimal
class NormalizationError(ValueError): code: str   # DATE_AMBIGUOUS, DATE_INVALID, AMOUNT_INVALID, ENUM_UNKNOWN, …
```

Enum value tables (`normalize_enum`, matched on the uppercased raw text, first hit wins):

| Field | Raw → value |
|---|---|
| `buy_sell` | PURCHASE / BUY / BOUGHT → `BUY`; SALE / SELL / SOLD → `SELL` |
| `deal_type` | REVERSE REPO → `REVERSE_REPO`; REPO → `REPO`; TREPS + BORROW → `TREPS_BORROW`; TREPS + LEND → `TREPS_LEND`; LAF / SDF / MSF / VRR / VRRR → `LAF`; AUCTION / ALLOTMENT → `PRIMARY_AUCTION`; PRIVATE PLACEMENT / EBP → `PRIMARY_PLACEMENT`; OUTRIGHT / PURCHASE / SALE / BUY / SELL → `OUTRIGHT` |
| `day_count` | ACTUAL/ACTUAL, ACT/ACT → `ACT/ACT`; ACTUAL/365, ACT/365 → `ACT/365`; ACT/364 → `ACT/364`; ACT/360 → `ACT/360`; 30/360, 30E/360 → `30/360` |
| `bid_type` | NON-COMPETITIVE / NON COMPETITIVE / NCB → `NON_COMPETITIVE`; COMPETITIVE → `COMPETITIVE` |
| `instrument_type` | `profile.instrument_patterns` over raw (+ security name) |
| `platform` | `profile.platforms` regex over raw; no hit → raw text unchanged |
| `settlement_mode` | `DVP[- ]?(III\|II\|I\|3\|2\|1)` → `DVP-III` / `DVP-II` / `DVP-I`; else raw |
| `broker` | `Direct`, `Direct (No Broker)`, `No Broker`, `None`, `N.A.`, `NA`, `-` → `null` (expected output of `samples/mock/02`) |

A normalisation failure keeps `raw`, sets `value=None` and adds an ERROR issue
(`VALUE_UNPARSEABLE`, or the specific code) for that field.

#### `derive.py`

```python
@dataclass(frozen=True, slots=True)
class Rule: name: str; target: str; fn: Callable[[DeriveCtx], tuple[Any, str | None, FieldValue | None] | None]
RULES: tuple[Rule, ...]                        # ordered, §4.12
def derive(fields: dict[str, FieldValue], doc: ExtractedDocument, profile: MarketProfile | None) -> None
```

Rules only fill fields that are still empty (never overwrite a mapped value), except the repo
→ top-level copy which also only fills empties. Each derived `FieldValue` has `method="derived"`,
`confidence=0.90`, `label` = source label (or `derived:<rule>` when computed), `bbox` of the source.

#### `validate.py`

```python
def validate(deal: dict, fields: dict[str, FieldValue], profile: MarketProfile | None) -> list[Issue]   # §4.13
def isin_check_digit_ok(isin: str) -> bool
def missing_required(deal: dict) -> list[str]
def low_confidence(fields: dict[str, FieldValue], required: list[str], threshold: float) -> list[str]
def days_30_360(d1: date, d2: date) -> int     # identical to tools/generate_mock_slips.py
```

#### `pipeline.py`

```python
def run(pdf: Path | bytes, *, file_name: str, sha256: str, templates: Sequence[TemplateDefinition],
        es: EngineSettings, draft: TemplateDefinition | None = None) -> EngineResult
def decide_status(*, doc: ExtractedDocument, match: TemplateMatchResult, market: MarketDetection,
                  missing: list[str], issues: list[Issue], low_conf: list[str]) -> SlipStatus
def build_deal(fields: dict[str, FieldValue], market: MarketDetection) -> dict[str, Any]
```

```
run():
  doc = extract(pdf, max_pages)                         # 2
  if not doc.has_text_layer: return result(UNREADABLE)
  mkt = detect_market(doc)                              # 3
  match = draft ? TemplateMatchResult(draft, score=jaccard(fp, draft.labels), matched=True, is_draft=True)
               : match_template(doc, templates, es.template_match_threshold)   # 4
  tpl = match.template if match.matched else None
  cands, kv_map, ignored = collect_candidates(doc, tpl)  # 5
  if tpl: apply_rules(doc, tpl, cands)                  # 6
  normalised = normalize each candidate (profile = mkt.profile)                 # 7
  fields, issues = resolve(cands, normalised)
  derive(fields, doc, mkt.profile)                      # 8
  if tpl: bump accepted_derived fields to 0.95
  deal = build_deal(fields, mkt)
  issues += validate(deal, fields, mkt.profile)         # 9
  missing = missing_required(deal); low = low_confidence(...)
  status = decide_status(...)                           # 10
  return EngineResult(...)
```

Any exception escaping `run` is caught by the service, which stores status `FAILED` (§8).

### 3.10 Export (`app/export/*`)

```python
# json_export.py
def to_json(result: ParseResult) -> bytes                      # model_dump_json(by_alias=True, indent=2)
def to_json_many(items: Sequence[tuple[str | None, ParseResult]]) -> bytes
# xml_export.py
def to_xml(result: ParseResult) -> bytes                       # root <parse_result schema_version="1">
def to_xml_many(items: Sequence[tuple[str | None, ParseResult]]) -> bytes   # root <deals>
def sanitize_tag(key: str) -> str
# excel_export.py
def to_xlsx(items: Sequence[tuple[str | None, ParseResult]], *, single: bool) -> bytes
```

Formats, element rules and sheet layouts: `docs/05_data_dictionary_and_outputs.md` §5–§7.

### 3.11 DB (`app/db/*`)

```python
# database.py
def make_engine(url: str) -> Engine            # SQLite: check_same_thread=False, pragmas, BEGIN IMMEDIATE
SessionLocal: sessionmaker[Session]            # expire_on_commit=False
def init_db(engine: Engine) -> None            # Base.metadata.create_all + install_audit_guards()
def install_audit_guards(conn: Connection) -> None   # SQLite triggers / PG function + triggers (§7.3)
class UTCDateTime(TypeDecorator):              # stores UTC, returns tz-aware UTC (SQLite drops tzinfo)
# models.py  (SQLAlchemy 2.x declarative, Mapped[...] annotations)
class Base(DeclarativeBase)
class Client, Slip, Template, TemplateVersion, AuditLog    # columns: §7
JSONType = JSON().with_variant(JSONB(), "postgresql")
```

---

## 4. Algorithms

### 4.1 Table classification

```
label_like(s):
    s = s.strip()
    if not s or len(s) > 60: return False
    if not any(ch.isalpha() for ch in s): return False
    chars = [c for c in s if not c.isspace()]
    return sum(c.isdigit() for c in chars) / len(chars) < 0.30

classify_table(rows):                      # rows already cleaned + ISIN-repaired, empty rows dropped
    ncols = max(len(r) for r in rows)
    share(xs) = count(true in xs) / count(xs non-empty)
    if ncols == 2 and share(label_like(r[0]) for r in rows) >= 0.8:            return "KV"
    if ncols == 4 and share(label_like(r[0]) and label_like(r[2]) for r in rows) >= 0.8: return "KV2"
    if len(rows) >= 2 and all(label_like(c) for c in rows[0] if c) \
       and count(c for c in rows[0] if c) >= 2:                                 return "GRID"
    return "UNKNOWN"                       # its words are released to text-line pairing

emit:
    KV   : for each row with row[0] and row[1]:    KeyValue(row[0], row[1], "table", cell bboxes)
    KV2  : as KV for (c0,c1) and (c2,c3)
    GRID : §4.3
```

The share threshold 0.8 **[A]** tolerates an occasional value-like label (e.g. `SGL / CSGL A/c`
passes anyway: 0 digits). Sample check: `samples/mock/01` → one KV table (24 rows);
`02` → KV, GRID, GRID, GRID, KV; `04` → KV (11 rows) + GRID (3 rows × 7 cols).

### 4.2 ISIN overflow repair (grid cells)

pdfplumber assigns characters by cell boundaries; narrow ISIN columns can receive the first
characters of the next cell. Observed on `samples/mock/02`: `['INE917S070328.2', '5% SIFL NCD 2029 (Secured)', …]`.

```
repair_isin_overflow(row):
    for j in range(len(row) - 1):
        c = row[j]
        if len(c) > 12 and re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d", c) and isin_check_digit_ok(c[:12]):
            overflow = c[12:]
            row[j]     = c[:12]
            row[j + 1] = overflow + row[j + 1]          # "8.2" + "5% SIFL…" → "8.25% SIFL…"
    return row
```

Applied to every row of every table before classification. The check-digit guard stops the rule
from cutting a legitimate longer token.

### 4.3 Grid with one or many value rows

```
_grid_pairs(rows, cells):
    header = rows[0]; body = rows[1:]
    if len(body) == 1:
        for j, h in enumerate(header):
            if h and body[0][j]: emit(key=h, value=body[0][j], source="grid",
                                      key_bbox=cells[0][j], value_bbox=cells[1][j])
    else:                                            # multi-row grid, e.g. repo legs
        for i, r in enumerate(body, start=1):
            row_label = r[0]
            for j in range(1, len(header)):
                if header[j] and r[j]:
                    emit(key=f"{row_label} {header[j]}", value=r[j], source="grid",
                         key_bbox=cells[0][j], value_bbox=cells[i][j])   # key_bbox = header cell [A]
```

Example (`samples/mock/04`): `First Leg Price = 99.8500`, `Second Leg Settlement Amount =
10,17,97,994.86`. Mapping resolves composite keys with `LEG_PREFIXES` + `LEG_SUFFIXES`
(`first leg` + `settlement amount` → `repo.leg1_amount`). `Leg Direction` values are unmapped.

### 4.4 Text-line pairing and label normalisation

```
_text_line_pairs(page, page_no, exclude=table_bboxes):
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
    words = [w for w in words if not inside_any(w, exclude)]
    lines = group words whose `top` differs by ≤ 3pt; sort lines by top, words by x0
    for line in lines:
        segs = split line where (w.x0 - prev.x1) > 10pt  or  w.text == "|"   ('|' tokens dropped;
               a '|' inside a word splits that word)
        pairs = []
        for seg in segs:                                        # 1) colon pairs
            t = join(seg)
            m = re.match(r"^(?P<k>[^:]{1,60}?)\s*:\s+(?P<v>\S.*)$", t)   # ': ' required → '11:42:17' is not split
            if m and label_like(m.k): pairs.append((m.k, m.v, bbox(label words), bbox(value words)))
        if pairs: emit all; continue
        if len(segs) == 2 and label_like(segs[0]) and has_alnum(segs[1]):         # 2) "Label   value"
            emit(segs[0], segs[1])
        elif len(segs) >= 4 and len(segs) % 2 == 0 and all(label_like(s) for s in segs[0::2]):
            emit consecutive (label, value) pairs
        else: skip                                              # headings, prose, signature rows
```

`samples/mock/04` line `Settlement: DVP-III through CCIL | Portfolio: Money Market - Liquidity | Dealer: P. Nair`
→ 3 pairs. The signature row `Dealer [107pt] Checker (Mid Office) [57pt] Authorised Signatory …`
has 3 label-like segments → skipped; the `________` row has no alnum → skipped.

```
normalise_label(label) -> (full, short):
    s = unicodedata.normalize("NFKC", label).lower().strip()
    full  = collapse_spaces(re.sub(r"[^a-z0-9]+", " ", s))
    short = collapse_spaces(re.sub(r"[^a-z0-9]+", " ", re.sub(r"\([^)]*\)", " ", s)))
    return full, (short or full)
```

| Label | full | short |
|---|---|---|
| `Face Value (INR)` | `face value inr` | `face value` |
| `Yield to Maturity (%)` | `yield to maturity` | `yield to maturity` |
| `Cut-off Price (per INR 100)` | `cut off price per inr 100` | `cut off price` |
| `Deal Date / Time` | `deal date time` | `deal date time` |
| `SGL / CSGL A/c` | `sgl csgl a c` | `sgl csgl a c` |

Lookup always tries `full` first, then `short`.

### 4.5 Label → field lookup (with abbreviations and fuzzy)

```
ABBREVIATIONS = {"dt": "date", "stl": "settlement", "sett": "settlement", "settl": "settlement",
                 "amt": "amount", "qty": "quantity", "cpty": "counterparty", "accr": "accrued",
                 "int": "interest", "mat": "maturity", "cons": "consideration", "yld": "yield",
                 "px": "price", "ref": "reference", "desc": "description", "sec": "security",
                 "nbr": "no", "num": "no", "number": "no"}

lookup_label(label, tpl):
    full, short = normalise_label(label)
    for k in (full, short):
        if tpl and k in tpl.ignore:                 return IGNORED
        if tpl and k in tpl.label_map:              return (tpl.label_map[k], "template", 1.00)   # "_ignore" → IGNORED
    for k in (full, short):
        if k in SYNONYMS:                           return (SYNONYMS[k], "synonym", 0.95)
        if leg := leg_lookup(k):                    return (leg, "synonym", 0.95)
    for k in (full, short):
        x = " ".join(ABBREVIATIONS.get(tok, tok) for tok in k.split())
        if x != k and (x in SYNONYMS or leg_lookup(x)):
                                                    return (SYNONYMS.get(x) or leg_lookup(x), "abbreviation", 0.90)
    for k in (full, short):
        if len(k) >= 4:                                                                    # [A]
            m = difflib.get_close_matches(k, SYNONYMS.keys() ∪ (tpl.label_map.keys() if tpl else ∅),
                                          n=1, cutoff=0.85)
            if m: r = SequenceMatcher(None, k, m[0]).ratio()
                  return (target(m[0]), "fuzzy", round(r * 0.85, 2))
    return None                                     # → unmapped
```

`Stl Dt` → `stl dt` → `settlement date` → `settlement_date` (abbreviation, 0.90).
`Deal Number` → `deal no` → `deal_id` (abbreviation). Fuzzy tops out at 0.85, below the 0.90
threshold, so a fuzzy-mapped required field always yields `NEEDS_REVIEW`.

### 4.6 Template fingerprint

`fingerprint(doc) = { normalise_label(kv.key).full for kv in doc.pairs if kv.source in {table, grid, text} }`.
Prose is excluded (its keys are synthetic).

### 4.7 Template matching

```
match_template(doc, templates, default_threshold):
    fp = fingerprint(doc); text = doc.text.lower()
    best = None
    for t in templates (active only):
        if t.keywords and not all(kw.lower() in text for kw in t.keywords): continue   # keyword gate [A]
        score = |fp ∩ t.labels| / |fp ∪ t.labels|  (0 if union empty), rounded 4 dp
        thr = t.match_threshold or default_threshold
        if score >= thr and (best is None or (score, match_count(t), updated_at(t)) > best.key):
            best = (t, score, thr)
    if best: return TemplateMatchResult(best.t, best.score, best.thr, matched=True)
    return TemplateMatchResult(None, max score seen or 0.0, default_threshold, matched=False)
```

### 4.8 Candidate resolution

```
RANK = {region: 1, regex: 2, template: 3, constant: 4, synonym: 5, abbreviation: 6, fuzzy: 7, text: 8}
SOURCE_ORDER = {table: 0, grid: 1, text: 2, prose: 3}
Prose-sourced candidates always get method "text" (0.80), whatever label matched.

resolve(cands):
    for field, cs in cands.items():
        cs = [c for c in cs if normalised value is not None] or cs     # prefer parseable
        cs.sort(key=(RANK[c.method], -c.confidence, SOURCE_ORDER[c.source], c.page, c.top, c.x0))
        win = cs[0]
        rivals = [c for c in cs[1:] if RANK[c.method] == RANK[win.method]
                                     and norm(c) is not None and norm(c) != norm(win)]
        if rivals: issue CONFLICTING_VALUES (WARNING, fields=[field]); win.confidence = min(win.confidence, 0.85)
        fields[field] = FieldValue(norm(win), win.raw, win.method, win.confidence, win.kv.key, win.kv.page, win.kv.value_bbox)
```

Constants only fill a field that has no candidate of rank ≤ 3 **[A]**. `samples/mock/03`: the
table's `Cut-off Yield (%) = 5.6512` (synonym, rank 5) beats prose `at a yield of 5.6400%` (text,
rank 8); different ranks → no conflict issue.

### 4.9 Market detection vote

| Signal | Detection | Market | Weight |
|---|---|---|---|
| `isin_prefix` | first 2 chars of each valid ISIN | `IN/US/GB/DE/JP` → same; `XS`, `EU` → `INTL`; other → none | 3 |
| `cusip` | 9-char CUSIP with valid check digit next to a `CUSIP` label | `US` | 3 |
| `sedol` | 7-char SEDOL with valid check digit next to a `SEDOL` label | `GB` | 3 |
| `currency` | `INR`, `Rs.`, `₹` → IN; `USD`, `US$` → US; `GBP`, `£` → GB; `JPY`, `¥` → JP; `EUR`, `€` → DE (weight 1) | as listed | 2 |
| `settlement_system` | CCIL, NSDL, CDSL, NCL, ICCL, RBI SGL → IN; DTC, Fedwire → US; CREST → GB; Clearstream Banking Frankfurt → DE; JASDEC, BOJ-NET → JP; Euroclear, Clearstream Luxembourg → INTL | as listed | 2 |
| `platform` | NDS-OM, CROMS, E-Kuber, NSE RFQ, BSE RFQ, EBP, TREPS → IN; TradeWeb / Bloomberg → no vote | IN | 2 |
| `name_style` | `\d+\.\d+% GS \d{4}`, `SDL`, `\d{2,3} DTB \d{8}`, `NCD` → IN; `UST`/`T \d+(\.\d+)? \d{2}/\d{2}/\d{2,4}` → US; `Gilt` → GB; `Bund`/`DBR` → DE; `JGB` → JP | as listed | 1 |
| `number_format` | Indian grouping `\b\d{1,2}(,\d{2})+,\d{3}(\.\d+)?\b` → IN; `\b\d{1,3}(\.\d{3})+,\d{2}\b` → DE; western grouping → no vote (ambiguous) | as listed | 1 |
| `bic` | `\b[A-Z]{4}(IN\|US\|GB\|DE\|JP)[A-Z0-9]{2}([A-Z0-9]{3})?\b` near `BIC`/`SWIFT` | country | 1 |

Weights are **[A]**; signals are those of baseline §4 step 3.

```
detect_market(doc):
    signals = collect (each signal type votes at most once per market)
    totals  = Σ weight per market
    if not totals: return UNKNOWN, confidence 0.0
    winner  = argmax(totals); conf = round(totals[winner] / Σ totals, 2)
    market  = winner if conf >= 0.70 else "UNKNOWN"          # below 0.70 → UNKNOWN (baseline)
    issuer_country = isin_country(first valid ISIN)           # None for XS/EU or no ISIN
    profile = get_profile(market)                             # None for UNKNOWN and (MVP) non-IN
    slip_locale = profile.locale if profile else locale_from_signals()   # e.g. "en-US"; None if unsure
    return MarketDetection(market, issuer_country, slip_locale, conf, signals, profile)
```

`market == "UNKNOWN"` adds WARNING `MARKET_UNCERTAIN`; `profile is None` adds WARNING
`MARKET_PROFILE_UNAVAILABLE`. Either forces `NEEDS_REVIEW` when a template matched (§4.15).
Without a profile, normalisation still runs with neutral rules (ISO / textual-month dates only,
no lakh/crore units, western grouping).

### 4.10 Date parsing

```
parse_date(raw, profile, hints):
    s = strip time tokens (\d{1,2}:\d{2}(:\d{2})?), parenthesised notes "(T+1)", ordinals (1st, 2nd…), commas
    ISO  ^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$                            → Y-M-D (always)
    TEXT d[-/ .]Mon[-/ .]yy(yy) | d Month yyyy | Month d yyyy             → unambiguous
    NUM  ^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$ :
         yy → 2000+yy if yy < 70 else 1900+yy                              [A]
         a > 12 ≥ b → D/M ;  b > 12 ≥ a → M/D ;  a == b → same either way
         else (both ≤ 12, differ):
            if hints.order (another date on the same slip was unambiguous) → use it       [A]
            elif profile → profile.date_order (IN: DMY)
            else → raise NormalizationError("DATE_AMBIGUOUS")
    COMPACT ^\d{8}$ : yyyymmdd if starts 19/20 and valid, else ddmmyyyy (DMY profile)
    construct date(); ValueError → NormalizationError("DATE_INVALID")
```

`DATE_AMBIGUOUS` → value `null`, raw kept, ERROR issue; the field is then missing if required.

### 4.11 Amount parsing (Indian grouping, lakh / crore)

```
parse_amount(raw, profile):
    s = raw.strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = remove currency tokens (INR, Rs., Rs, ₹, USD, …), "(…)" notes, "/-" suffix, leading "-"
    m = re.fullmatch(r"(?P<num>[\d,]*\.?\d+)\s*(?P<unit>[A-Za-z]+\.?)?", s) or raise AMOUNT_INVALID
    num = m.num
    if "," in num and not (INDIAN.fullmatch(num) or WESTERN.fullmatch(num)): raise AMOUNT_INVALID
        INDIAN  = ^\d{1,2}(,\d{2})*,\d{3}(\.\d+)?$         5,00,00,000.00
        WESTERN = ^\d{1,3}(,\d{3})+(\.\d+)?$               50,000,000.00
    mult = 1
    if m.unit: mult = (profile.amount_units if profile else {}).get(m.unit.lower().rstrip("."))
               or raise AMOUNT_INVALID (unknown unit)
    d = Decimal(num.replace(",", "")) * mult
    return -d if neg else d
```

`5,00,00,000.00` → `50000000.00`; `5.00 Cr` → `50000000.00`; `50 Lakh` → `5000000`. Scale per
field kind is applied afterwards (`normalize.scale`, doc 05 §8).

### 4.12 Derivation rules (ordered)

| # | Target | Rule | Example |
|---|---|---|---|
| D1 | `deal_type` | `normalize_enum("deal_type", …)` over the `deal_type` raw, else over full text (`Primary Auction`, `Allotment`, `Reverse Repo`) | 03: text → `PRIMARY_AUCTION` |
| D2 | `buy_sell` | from deal-type raw: PURCHASE/BUY/BOUGHT → BUY, SALE/SELL/SOLD → SELL; `REVERSE_REPO`, `PRIMARY_AUCTION`, `PRIMARY_PLACEMENT`, `TREPS_LEND` → BUY; `REPO`, `TREPS_BORROW` → SELL | 01: `OUTRIGHT PURCHASE` → BUY |
| D3 | `instrument_type` | `profile.instrument_patterns` over security type raw, then `security_name`; ISIN `INE…` + no pattern → `CORPORATE_BOND` | 04: `6.99% GS 2031` → GSEC |
| D4 | `platform` | `profile.platforms` over deal-type raw, then full text | 02: `SELL - Outright (OTC, reported on NSE RFQ)` → `OTC / NSE RFQ` |
| D5 | `coupon_rate` | `^(\d+(\.\d+)?)%` in `security_name` | `7.10% GS 2034` → 7.10 |
| D6 | `coupon_frequency` | SEMI-ANNUAL/HALF-YEARLY → 2, ANNUAL/YEARLY → 1, QUARTERLY → 4, MONTHLY → 12 in coupon raw; else `profile.default_coupon_frequency[instrument_type]`; discount instruments → none | 02: `8.25% Annual` → 1 |
| D7 | `day_count` | parenthesised day count in accrued-days raw (`167 (30/360)`), else `profile.default_day_count[instrument_type]` | 01 → `30/360` |
| D8 | `tenor_days` | `(\d{2,3}) DTB` / `(\d{2,3})[- ]day` in name or prose; else `maturity_date − settlement_date` for TBILL/CMB/CP/CD | 03 → 182 |
| D9 | `maturity_date` | `DTB (\d{8})` ddmmyyyy in name; else `settlement_date + tenor_days` | `182 DTB 25032027` → 2027-03-25 |
| D10 | `currency` | `(INR)`-style suffix in any mapped label, or profile currency when market has a profile | 01 → INR |
| D11 | `settlement_mode` | DVP token anywhere in settlement text | `Through NSE Clearing Ltd (DVP-I), T+1` → DVP-I |
| D12 | `counterparty` | `PRIMARY_AUCTION` with platform `RBI E-Kuber` → `Reserve Bank of India` | 03 |
| D13 | `repo.repo_days` | `leg2_date − leg1_date` (days) | 04 → 3 |
| D14 | `repo.repo_interest` | `leg2_amount − leg1_amount` | 04 → 45161.53 |
| D15 | top level from repo leg 1 | `settlement_date ← leg1_date`, `price ← leg1_price`, `accrued_days ← leg1_accrued_days`, `accrued_interest ← leg1_accrued_interest`, `consideration ← leg1_amount`; `trade_date ← leg1_date` if still empty | 04: consideration 101752833.33 |
| D16 | `face_value` | `quantity × face_value_per_unit` | 02: 250 × 100000 |
| D17 | `quantity` | `face_value / face_value_per_unit` when integral | |
| D18 | `principal_amount` | `face_value × price / 100` (cash scale) | |
| D19 | `discount_amount` | discount instruments: `face_value − consideration` | |
| D20 | `price` (TBILL/CMB) | `100 / (1 + yield/100 × tenor_days / 364)` rounded 4 dp; CP/CD basis 365 | 03 check: 97.2520 |
| D21 | `identifiers.isin` | copy of `isin` | |
| D22 | market fields | `market`, `issuer_country`, `slip_locale`, `market_confidence` from `MarketDetection` (not a `FieldValue`) | |

### 4.13 Validation checks

Arithmetic in `Decimal` under `localcontext(prec=34)`; comparisons against quantised values.
A check is skipped when any input is null. `price_tol(fv, price) = max(0.01, fv × 0.5 × 10^-s / 100)`
where `s` = decimal places of the price as printed (covers prices rounded to 4 dp) **[A]**.

| Code | Severity | Rule | Tolerance |
|---|---|---|---|
| `ISIN_FORMAT` | ERROR | `^[A-Z]{2}[A-Z0-9]{9}\d$` | exact |
| `ISIN_CHECK_DIGIT` | ERROR | ISO 6166 Luhn over letter-expanded digits (as `isin_with_check`) | exact |
| `ENUM_INVALID` | ERROR | enum fields hold an allowed value | exact |
| `SETTLEMENT_BEFORE_TRADE` | ERROR | `settlement_date ≥ trade_date` | – |
| `MATURITY_NOT_AFTER_SETTLEMENT` | ERROR | `maturity_date > settlement_date` | – |
| `WEEKEND_SETTLEMENT` | ERROR | `settlement_date` (and repo leg dates) not in `profile.weekend` | – |
| `HOLIDAY_SETTLEMENT` | WARNING | not in `profile.holidays` **[A] severity** | – |
| `PRINCIPAL_MISMATCH` | ERROR | `face_value × price / 100 ≈ principal_amount` | `price_tol` |
| `ACCRUED_DAYS_MISMATCH` | WARNING | days(last_coupon_date → settlement_date) per `day_count` (30/360 formula, else actual) = `accrued_days` | exact |
| `ACCRUED_MISMATCH` | ERROR | `face_value × coupon_rate/100 × accrued_days / basis ≈ accrued_interest`; basis 360 (30/360, ACT/360), 365 (ACT/365, ACT/ACT **[A]**) | ±0.01 |
| `CONSIDERATION_MISMATCH` | ERROR | coupon instruments (outright): `principal_amount + accrued_interest ≈ consideration` | ±0.01 |
| `DISCOUNT_MISMATCH` | ERROR | discount instruments: `face_value − consideration ≈ discount_amount` | ±0.01 |
| `DISCOUNT_PRICE_MISMATCH` | ERROR | discount instruments: `face_value × price / 100 ≈ consideration` | `price_tol` |
| `TBILL_PRICE_MISMATCH` | WARNING | `100 / (1 + yield × tenor/36400) ≈ price` (ACT/364) | ±0.0001 |
| `QUANTITY_MISMATCH` | ERROR | `quantity × face_value_per_unit = face_value` | exact |
| `REPO_LEG_AMOUNT_MISMATCH` | ERROR | per leg: `face_value × leg_price/100 + leg_accrued_interest ≈ leg_amount` | `price_tol` (04 leg 2: 1e8 × 0.00005 / 100 = 50; actual diff 11.53) |
| `REPO_INTEREST_MISMATCH` | ERROR | `leg1_amount + repo_interest ≈ leg2_amount` | ±0.01 |
| `REPO_INTEREST_CALC` | WARNING | `leg1_amount × repo_rate/100 × repo_days / basis(repo.day_count) ≈ repo_interest` | ±0.01 |
| `REPO_DAYS_MISMATCH` | ERROR | `leg2_date − leg1_date = repo_days` | exact |
| `CONFLICTING_VALUES`, `VALUE_UNPARSEABLE`, `DATE_AMBIGUOUS`, `RULE_NO_MATCH`, `MARKET_UNCERTAIN`, `MARKET_PROFILE_UNAVAILABLE` | as above | raised in earlier steps | – |

`missing_required` is separate from `validation`: the list of required paths (`required_fields(deal_type)`)
whose deal value is null. If `deal_type` itself is missing, `BASE_REQUIRED` applies.

### 4.14 Confidence post-processing

```
if tpl: for f in tpl.accepted_derived: if f in fields and fields[f].method in {derived, text, fuzzy}:
            fields[f].confidence = 0.95
low_confidence = [p for p in required_fields(deal_type) if p in fields and fields[p].confidence < threshold]
```

### 4.15 Status decision (baseline §3)

```
decide_status(doc, match, market, missing, issues, low_conf):
    if not doc.has_text_layer:                     return UNREADABLE
    if not match.matched:                          return NEW_TEMPLATE
    if missing or any(i.severity == ERROR for i in issues) or low_conf \
       or market.profile is None or market.market == "UNKNOWN":
                                                   return NEEDS_REVIEW
    return PARSED
# FAILED is set by the service when run() raises; APPROVED only by approve().
```

WARNING issues do not block `PARSED` **[A]**. A preview with a draft mapping reports the status
the slip *would* get with that mapping (`template.is_draft = true`).

### 4.16 Audit row hashing

```
HASHED = ("event_id", "occurred_at", "actor_type", "actor_id", "actor_name", "action",
          "entity_type", "entity_id", "status_before", "status_after", "changes", "details",
          "request_id", "ip_address", "user_agent", "outcome", "error", "prev_hash")

canonical_json(o) = json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                               default=lambda x: format(x, "f") if isinstance(x, Decimal) else str(x))

record(...):
    lock_chain(db)                        # SQLite: txn is BEGIN IMMEDIATE; PG: pg_advisory_xact_lock(K)
    prev = SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1  or GENESIS_HASH
    row  = {event_id: uuid4(), occurred_at: now_utc() formatted "%Y-%m-%dT%H:%M:%S.%fZ", …, prev_hash: prev}
    row_hash = sha256(canonical_json({k: row[k] for k in HASHED}).encode("utf-8")).hexdigest()
    INSERT audit_log(... , prev_hash=prev, row_hash=row_hash)       # flush; commit with caller

verify_chain(db):
    prev = GENESIS_HASH
    for r in SELECT * FROM audit_log ORDER BY id (streamed in batches):
        if r.prev_hash != prev:                        return ok=False, first_bad_id=r.id, reason=prev_hash_mismatch
        if compute_row_hash(r, r.prev_hash) != r.row_hash: return ok=False, first_bad_id=r.id, reason=row_hash_mismatch
        prev = r.row_hash
    return ok=True, checked=n, last_hash=prev
```

`prev_hash` is inside the hashed payload, so `row_hash = SHA-256(canonical_json(row fields + prev_hash))`.
`occurred_at` is hashed as the exact string written; on read `UTCDateTime` returns aware UTC and
`verify_chain` re-formats it with the same pattern. `changes`/`details` are hashed as parsed JSON
objects (key-sorted), so storage re-serialisation (JSONB) does not break the chain.

---

## 5. Slip status state machine

```
                    POST /slips  (upload + parse)
                              │
     ┌───────────────┬────────┴─────────┬────────────────┬──────────────┐
     ▼               ▼                  ▼                ▼              ▼
 ┌────────┐   ┌──────────────┐   ┌──────────────┐  ┌────────────┐  ┌────────┐
 │ PARSED │   │ NEEDS_REVIEW │   │ NEW_TEMPLATE │  │ UNREADABLE │  │ FAILED │
 └───┬────┘   └──────┬───────┘   └──────┬───────┘  └─────┬──────┘  └───┬────┘
     │  POST /approve │  POST /approve   │ POST /approve  │             │
     └───────────────►┴────────┬─────────┘                │             │
                               ▼                          │             │
                         ┌──────────┐                     │             │
                         │ APPROVED │  (final)            │             │
                         └────┬─────┘                     │             │
                              │ POST /reparse?force=true  │             │
                              ▼                           ▼             ▼
                  ┌───────────────────────────────────────────────────────┐
                  │ re-evaluated by pipeline → PARSED | NEEDS_REVIEW |     │
                  │ NEW_TEMPLATE | UNREADABLE | FAILED                     │
                  └───────────────────────────────────────────────────────┘
      any non-APPROVED ──POST /reparse──► re-evaluated (same target set)
      any status ──DELETE /slips/{id}──► row + PDF removed (audit rows kept)
      POST /preview ── no transition (result not saved)
```

| From | To | Trigger | Guard | Audit |
|---|---|---|---|---|
| – | `PARSED` / `NEEDS_REVIEW` / `NEW_TEMPLATE` / `UNREADABLE` / `FAILED` | `POST /slips`, `POST /slips/batch` | `decide_status` / exception | `SLIP_UPLOADED`, `SLIP_PARSED` |
| `PARSED`, `NEEDS_REVIEW`, `NEW_TEMPLATE` | `APPROVED` | `POST /slips/{id}/approve` | no missing required; no ERROR issues unless `accept_validation_issues=true`; market ≠ UNKNOWN | `TEMPLATE_CREATED`/`TEMPLATE_VERSION_ADDED` + `SLIP_APPROVED` |
| any non-`APPROVED` | re-evaluated | `POST /slips/{id}/reparse` | – | `SLIP_REPARSED` |
| `APPROVED` | re-evaluated | `POST /slips/{id}/reparse?force=true` | `force=true` (else 409) | `SLIP_REPARSED` (`details.force=true`) |
| `UNREADABLE`, `FAILED` | `APPROVED` | – | not allowed → 409 `invalid-state` | – |
| any | deleted | `DELETE /slips/{id}` | – | `SLIP_DELETED` |

A forced re-parse of an `APPROVED` slip clears `approved_by / approved_actor_name / approved_at`
(the approval stays in the audit trail) **[A]**.

---

## 6. Sequence diagrams

### 6.1 `POST /api/v1/slips`

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant MW as Middleware
    participant R as slips router
    participant A as auth
    participant S as slip_service
    participant T as template_service
    participant E as engine.pipeline
    participant DB as DB
    participant FS as data/uploads
    C->>MW: POST /api/v1/slips (multipart file, Authorization: Basic)
    MW->>R: request_id set
    R->>A: require_client()
    A->>DB: SELECT client; Argon2 verify
    A-->>R: ClientPrincipal
    R->>S: create_slip(upload, ctx)
    S->>FS: stream to .tmp-<uuid> (sha256, size ≤ 10 MB, %PDF- magic)
    S->>DB: SELECT slips WHERE sha256=? (read txn, closed)
    Note over S: sha256 hit → duplicate_of = earlier slip id (still stored as a new slip, baseline)
        S->>S: check pages ≤ 20 (pdfplumber)
        S->>T: load_active_definitions()
        S->>E: run(tmp, templates, settings)  (no txn open)
        E-->>S: EngineResult (or exception → FAILED)
        S->>DB: BEGIN IMMEDIATE
        S->>DB: re-check sha256; INSERT slips(status, result_json, duplicate_of, …)
        S->>DB: UPDATE templates match_count (if matched)
        S->>DB: audit SLIP_UPLOADED; audit SLIP_PARSED (status_after)
        S->>FS: rename tmp → <slip_id>.pdf
        S->>DB: COMMIT
        S-->>R: slip
        R-->>C: 201 SlipOut {duplicate, duplicate_of}, Location: /api/v1/slips/{id}
```

If the commit fails the renamed file is unlinked; if the rename fails the transaction is rolled back.

### 6.2 `POST /api/v1/slips/{id}/preview`

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant S as slip_service
    participant T as template_service
    participant E as engine.pipeline
    participant DB as DB
    C->>S: POST /slips/{id}/preview  MappingSpec
    S->>DB: SELECT slip (404 if absent)
    S->>S: validate spec (field paths, regex, bbox within page_sizes) → 422
    S->>T: base = spec.template_id ? current definition : matched template (if any)
    T-->>S: build_definition(spec, base) → draft TemplateDefinition (not stored)
    S->>E: run(stored PDF, draft=draft)
    E-->>S: EngineResult (template.is_draft=true)
    S->>DB: BEGIN; audit SLIP_PREVIEWED {draft summary}; COMMIT
    S-->>C: 200 ParseResult (slip row unchanged)
```

### 6.3 `POST /api/v1/slips/{id}/approve`

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant S as slip_service
    participant T as template_service
    participant E as engine.pipeline
    participant DB as DB
    C->>S: POST /slips/{id}/approve  ApproveRequest
    S->>DB: SELECT slip → 404 / status check (UNREADABLE, FAILED, APPROVED → 409)
    S->>S: validate spec → 422
    S->>T: base = template_id ? get current definition (404 if unknown) : None
    S->>E: run(stored PDF, draft = build_definition(spec, base))
    E-->>S: EngineResult
    alt missing_required or market UNKNOWN
        S-->>C: 422 approval-blocked (errors = missing fields)
    else ERROR issues and accept_validation_issues = false
        S-->>C: 409 validation-not-accepted (errors = issues)
    else ok
        S->>DB: BEGIN IMMEDIATE
        S->>DB: SELECT slip status again (optimistic guard; changed → 409)
        alt template_id is null
            T->>DB: INSERT templates(id=slugify(template_name), current_version=1)
            T->>DB: INSERT template_versions(version=1, definition_json, source_slip_id)
            T->>DB: audit TEMPLATE_CREATED
        else existing template
            T->>DB: INSERT template_versions(version=current+1, …)  UNIQUE(template_id,version)
            T->>DB: UPDATE templates SET current_version=current+1, updated_at
            T->>DB: audit TEMPLATE_VERSION_ADDED {diff}
        end
        S->>DB: UPDATE slips SET status=APPROVED, template_id, template_version, match_score,<br/>result_json (status APPROVED), approved_mapping_json, approved_by, approved_actor_name, approved_at
        S->>DB: audit SLIP_APPROVED (status_before → APPROVED, changes)
        S->>DB: COMMIT  (all or nothing)
        S-->>C: 200 SlipOut (status APPROVED)
    end
```

`IntegrityError` on `UNIQUE(template_id, version)` (concurrent approvals on one template) →
rollback → 409 `conflict`; the client retries.

### 6.4 `GET /api/v1/slips/{id}/export?format=xlsx`

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as slips router
    participant X as export_service
    participant EX as export.excel_export
    participant DB as DB
    C->>R: GET /slips/{id}/export?format=xlsx
    R->>X: export_slip(id, xlsx, ctx)
    X->>DB: SELECT slip (404)
    X->>X: ParseResult.model_validate(result_json)
    X->>EX: to_xlsx([(id, result)], single=True)
    EX-->>X: bytes
    X->>DB: BEGIN; audit SLIP_EXPORTED {format: xlsx}; COMMIT
    X-->>R: ExportFile
    R-->>C: 200 application/vnd.openxmlformats-officedocument.spreadsheetml.sheet<br/>Content-Disposition: attachment; filename="GS_NDSOM_2026_004571.xlsx"
```

`UNREADABLE`/`FAILED` slips export too (empty deal, status column shows why) **[A]**.

### 6.5 Authentication failure

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant MW as RequestIdMiddleware
    participant A as auth.require_client
    participant L as FailureLimiter
    participant AU as audit_service
    participant DB as DB
    C->>MW: GET /api/v1/slips (bad or missing Authorization)
    MW->>A: request_id
    A->>L: check(ip)
    alt blocked
        L-->>A: AuthBlockedError(retry_after)
        A-->>C: 429 auth-blocked, Retry-After: <s>
    else not blocked
        A->>DB: SELECT client (or none) ; Argon2 verify (dummy hash if none)
        A->>AU: record_standalone(AUTH_FAILED, FAILURE, actor_id=<given id>, details.reason)
        AU->>DB: BEGIN; INSERT audit_log; COMMIT
        A->>L: record_failure(ip)
        opt threshold reached (10 in 300 s)
            A->>AU: record_standalone(AUTH_BLOCKED, details {ip, block_seconds: 900})
        end
        A-->>C: 401 unauthorized, WWW-Authenticate: Basic realm="bonds-parser", X-Request-ID
    end
```

---

## 7. Database physical design

SQLAlchemy types in brackets map to SQLite / PostgreSQL. All timestamps are `UTCDateTime`
(`TEXT` ISO in SQLite, `TIMESTAMPTZ` in PostgreSQL). Indexes other than PK/UNIQUE and those named in
the baseline (`slips.sha256`, `slips.status`) are **[A]**.

### 7.1 Tables

#### `clients`

| Column | Type | Null | Default | Index / constraint |
|---|---|---|---|---|
| `id` | INTEGER | no | autoincrement | PK |
| `client_id` | VARCHAR(64) | no | – | UNIQUE `uq_clients_client_id` |
| `secret_hash` | VARCHAR(255) | no | – | Argon2 encoded hash |
| `description` | VARCHAR(255) | yes | NULL | |
| `is_active` | BOOLEAN | no | `TRUE` | |
| `created_at` | TIMESTAMP | no | now (app) | |
| `last_used_at` | TIMESTAMP | yes | NULL | |
| `rotated_at` | TIMESTAMP | yes | NULL | |

#### `slips`

| Column | Type | Null | Default | Index / constraint |
|---|---|---|---|---|
| `id` | CHAR(36) / UUID | no | `uuid4()` (app) | PK |
| `file_name` | VARCHAR(255) | no | – | original name, path components stripped |
| `file_size` | INTEGER | no | – | bytes, `CHECK (file_size > 0)` |
| `sha256` | CHAR(64) | no | – | `ix_slips_sha256` |
| `storage_path` | VARCHAR(512) | no | – | relative to `BONDS_DATA_DIR` (`uploads/<id>.pdf`) |
| `page_count` | INTEGER | yes | NULL | |
| `status` | VARCHAR(16) | no | – | `ix_slips_status`; `CHECK (status IN ('PARSED','NEEDS_REVIEW','NEW_TEMPLATE','APPROVED','UNREADABLE','FAILED'))` |
| `market` | VARCHAR(8) | yes | NULL | `ix_slips_market` |
| `issuer_country` | CHAR(2) | yes | NULL | |
| `template_id` | VARCHAR(100) | yes | NULL | FK → `templates.id` ON DELETE RESTRICT; `ix_slips_template_id` |
| `template_version` | INTEGER | yes | NULL | |
| `match_score` | NUMERIC(5,4) | yes | NULL | |
| `result_json` | JSON / JSONB | yes | NULL | serialised `ParseResult` |
| `approved_mapping_json` | JSON / JSONB | yes | NULL | the `ApproveRequest` as sent |
| `duplicate_of` | CHAR(36) / UUID | yes | NULL | FK → `slips.id` ON DELETE SET NULL |
| `created_by` | VARCHAR(64) | no | – | client_id |
| `created_at` | TIMESTAMP | no | now | `ix_slips_created_at` |
| `updated_at` | TIMESTAMP | no | now | set on every update (app) |
| `approved_by` | VARCHAR(64) | yes | NULL | client_id |
| `approved_actor_name` | VARCHAR(200) | yes | NULL | `X-Actor-Name` |
| `approved_at` | TIMESTAMP | yes | NULL | |

#### `templates`

| Column | Type | Null | Default | Index / constraint |
|---|---|---|---|---|
| `id` | VARCHAR(100) | no | – | PK (slug `^[a-z0-9_]+$`, app-validated) |
| `name` | VARCHAR(200) | no | – | |
| `description` | TEXT | yes | NULL | |
| `market` | VARCHAR(8) | no | – | |
| `is_active` | BOOLEAN | no | `TRUE` | `ix_templates_is_active` |
| `current_version` | INTEGER | no | – | `CHECK (current_version >= 1)` |
| `match_count` | INTEGER | no | `0` | |
| `last_matched_at` | TIMESTAMP | yes | NULL | |
| `created_by` | VARCHAR(64) | no | – | |
| `created_at` | TIMESTAMP | no | now | |
| `updated_at` | TIMESTAMP | no | now | |

#### `template_versions`

| Column | Type | Null | Default | Index / constraint |
|---|---|---|---|---|
| `id` | INTEGER | no | autoincrement | PK |
| `template_id` | VARCHAR(100) | no | – | FK → `templates.id` ON DELETE RESTRICT |
| `version` | INTEGER | no | – | `UNIQUE (template_id, version)` `uq_template_versions_tid_ver` |
| `definition_json` | JSON / JSONB | no | – | baseline §6 shape |
| `source_slip_id` | CHAR(36) / UUID | yes | NULL | no FK: provenance survives slip deletion **[A]** |
| `note` | TEXT | yes | NULL | |
| `created_by` | VARCHAR(64) | no | – | |
| `created_at` | TIMESTAMP | no | now | |

#### `audit_log`

| Column | Type | Null | Default | Index / constraint |
|---|---|---|---|---|
| `id` | INTEGER (SQLite `AUTOINCREMENT`) / BIGINT IDENTITY | no | auto | PK (never reused) |
| `event_id` | CHAR(36) / UUID | no | `uuid4()` | UNIQUE |
| `occurred_at` | TIMESTAMP | no | now | `ix_audit_occurred_at` |
| `actor_type` | VARCHAR(10) | no | – | `CHECK (actor_type IN ('client','system','cli'))` |
| `actor_id` | VARCHAR(64) | yes | NULL | `ix_audit_actor_id` |
| `actor_name` | VARCHAR(200) | yes | NULL | |
| `action` | VARCHAR(40) | no | – | `ix_audit_action` |
| `entity_type` | VARCHAR(20) | yes | NULL | `ix_audit_entity (entity_type, entity_id)`; values `slip`, `template`, `client`, `document`, `audit`, `auth` |
| `entity_id` | VARCHAR(100) | yes | NULL | (composite index above) |
| `status_before` | VARCHAR(16) | yes | NULL | |
| `status_after` | VARCHAR(16) | yes | NULL | |
| `changes` | JSON / JSONB | yes | NULL | `{"field": [old, new]}` |
| `details` | JSON / JSONB | yes | NULL | action-specific (format, duplicate, reason, …); never slip values beyond ids |
| `request_id` | VARCHAR(128) | yes | NULL | |
| `ip_address` | VARCHAR(45) | yes | NULL | IPv6-safe |
| `user_agent` | VARCHAR(512) | yes | NULL | |
| `outcome` | VARCHAR(8) | no | – | `CHECK (outcome IN ('SUCCESS','FAILURE'))` |
| `error` | TEXT | yes | NULL | |
| `prev_hash` | CHAR(64) | no | – | UNIQUE (a fork in the chain is impossible) **[A]** |
| `row_hash` | CHAR(64) | no | – | UNIQUE |

### 7.2 SQLite connection setup

```python
@event.listens_for(engine, "connect")
def _sqlite_on_connect(dbapi_conn, _):
    dbapi_conn.isolation_level = None                 # let SQLAlchemy emit BEGIN itself
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()

@event.listens_for(engine, "begin")
def _sqlite_begin(conn):
    conn.exec_driver_sql("BEGIN IMMEDIATE")           # single writer; serialises the audit chain
```

### 7.3 Append-only guards

SQLite (installed by `init_db`, idempotent):

```sql
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE not allowed');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE not allowed');
END;

-- [A] template versions are immutable as well
CREATE TRIGGER IF NOT EXISTS template_versions_no_update
BEFORE UPDATE ON template_versions
BEGIN
    SELECT RAISE(ABORT, 'template_versions are immutable');
END;
```

PostgreSQL (Alembic migration, after MVP):

```sql
CREATE OR REPLACE FUNCTION audit_log_block_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_block_change();
CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block_change();

-- least privilege: the app role can only read and append
REVOKE ALL ON audit_log FROM bonds_app;
GRANT SELECT, INSERT ON audit_log TO bonds_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO bonds_app;
-- tables are owned by bonds_owner (migrations only); bonds_app never owns objects
```

PostgreSQL notes: JSON columns are `JSONB` (GIN index on `slips.result_json -> 'deal'` optional
for search); UUID columns are native `UUID`; the chain lock is
`SELECT pg_advisory_xact_lock(7307206734)` **[A]** inside the writing transaction; isolation level
READ COMMITTED is sufficient because of the lock and `UNIQUE(prev_hash)`. The retention/archive job
(`BONDS_AUDIT_RETENTION_YEARS = 8`) runs as `bonds_owner`, exports the aged segment together with
its last `row_hash` as a checkpoint, and is itself audited.

---

## 8. Error handling

Problem `type` URIs are `urn:bonds-parser:problem:<slug>` **[A]**. Every problem body carries
`request_id`; `instance` is the request path.

| Exception (`app/errors.py`) | Raised by | HTTP | `type` slug | Notes |
|---|---|---|---|---|
| `BadRequestError` | services / api | 400 | `bad-request` | malformed query value, unknown `format`, invalid payload shape (baseline §7) |
| `AuthError` | auth | 401 | `unauthorized` | `WWW-Authenticate: Basic realm="bonds-parser"`; generic detail |
| `AuthBlockedError` | auth limiter | 429 | `auth-blocked` | `Retry-After` header |
| `NotFoundError` | services | 404 | `not-found` | slip / template / file |
| `InvalidStateError` | slip_service | 409 | `invalid-state` | approve on `APPROVED`/`UNREADABLE`/`FAILED`; reparse `APPROVED` without `force` |
| `ConflictError` | services (IntegrityError, stale status) | 409 | `conflict` | retryable |
| `PayloadTooLargeError` | upload | 413 | `payload-too-large` | > `BONDS_MAX_UPLOAD_MB` |
| `UnsupportedMediaTypeError` | upload | 415 | `unsupported-media-type` | not `%PDF-` |
| `TooManyPagesError` | extract / upload | 422 | `too-many-pages` | > `BONDS_MAX_PAGES` |
| `EncryptedPdfError` | extract | 422 | `encrypted-pdf` | pikepdf support after MVP |
| `InvalidPdfError` | extract | 422 | `invalid-pdf` | pdfplumber cannot open |
| `MappingValidationError` | schemas / slip_service | 422 | `invalid-mapping` | `errors[]` per bad entry |
| `ApprovalBlockedError` | slip_service | 422 | `approval-blocked` | `errors[]` = missing required fields / market UNKNOWN |
| `ValidationNotAcceptedError` | slip_service | 409 | `validation-not-accepted` | `errors[]` = ERROR issues; resend with `accept_validation_issues=true` |
| `RequestValidationError` (FastAPI) | framework | 422 | `request-validation` | pydantic errors in `errors[]` |
| `ConfigError` | config | – | – | startup abort |
| `OperationalError` (DB locked / down) | SQLAlchemy | 503 | `service-unavailable` | `Retry-After: 1` |
| any other `Exception` | anywhere | 500 | `internal-error` | detail hidden; stack logged with request_id |

Pipeline failures on a stored slip are **not** HTTP errors: the service catches the exception,
stores the slip with status `FAILED`, writes `SLIP_PARSED` with `outcome=FAILURE`,
`error=<ExceptionType>: <message>` and returns 201. `UNREADABLE` likewise returns 201.
Failures of a request that has its own business transaction roll it back, then an audit row with
`outcome=FAILURE` is written by `record_standalone` for state-changing endpoints (approve, reparse,
delete, template writes).

---

## 9. Runtime concerns

### 9.1 Transactions and concurrency

| Rule | Detail |
|---|---|
| One session per request | `get_db`; services commit explicitly. |
| No transaction across the pipeline | Read phase → transaction ended → `pipeline.run` → write phase in one transaction (BEGIN IMMEDIATE on SQLite). A slow PDF never holds the DB lock. |
| Business write + audit atomic | Audit rows are inserted in the same transaction as the change (D9). |
| Audit chain serialisation | SQLite: single writer via BEGIN IMMEDIATE; PostgreSQL: `pg_advisory_xact_lock`; both backed by `UNIQUE(prev_hash)`. |
| Approve races | Status re-read inside the write transaction; `UNIQUE(template_id, version)` → 409. |
| Counters | `match_count = match_count + 1` in SQL (no read-modify-write). |
| Workers | MVP: one Uvicorn worker (SQLite + in-process limiter). Multiple workers require PostgreSQL and a shared limiter. |

### 9.2 Idempotency

- `POST /slips`: every upload is stored as a new slip (baseline "Duplicate uploads"). If the SHA-256
  matches an earlier slip, the new row gets `duplicate_of = <earliest slip id>`, the response has
  `duplicate: true`, and `SLIP_UPLOADED` records `details.duplicate_of`. The lookup is repeated inside
  the write transaction (SQLite writer lock; PostgreSQL `pg_advisory_xact_lock(hashtextextended(sha256, 0))`)
  so concurrent uploads of one file all point at the same original.
- `POST /parse` is naturally idempotent (nothing stored).
- `approve` is not repeatable: a second call gets 409 `invalid-state`.

### 9.3 File handling

| Step | Rule |
|---|---|
| Receive | stream to `data/uploads/.tmp-<uuid>` in 64 KiB chunks; hash and count on the fly; abort at `BONDS_MAX_UPLOAD_MB`. |
| Check | magic `%PDF-`; pdfplumber open; not encrypted; `pages ≤ BONDS_MAX_PAGES`. |
| Store | `os.replace(tmp, uploads/<slip_id>.pdf)` inside the write phase; `storage_path` relative to `BONDS_DATA_DIR`. |
| Names | client `file_name` stored for display only (basename, control chars removed, ≤ 255); never used in paths. |
| Serve | `FileResponse(path, media_type="application/pdf", filename=file_name)`; path resolved from DB, never from input. |
| Delete | DB row delete + audit commit, then unlink the PDF (a leftover file is harmless; a missing row is not). |
| Cleanup | `.tmp-*` older than 1 h removed at startup **[A]**. |
| `/parse` | temp file in the OS temp dir, deleted in `finally`. |

### 9.4 Logging points

Stdlib `logging` in MVP (structlog after MVP), logger per module, every record carries `request_id`
(contextvar filter). Never logged: `Authorization`, secrets, secret hashes, PDF bytes, extracted
values (counterparty, amounts). Deal ids and slip ids are allowed.

| Where | Level | Message fields |
|---|---|---|
| `AccessLogMiddleware` | INFO | method, path, status, duration_ms, client_id, request_id |
| auth failure / block | WARNING | reason, ip, attempted client_id |
| upload received | INFO | slip_id, size, pages, sha256[:12], duplicate |
| pipeline stages | DEBUG | stage, duration_ms, pairs count, template score |
| template decision | INFO | slip_id, template_id, version, score, matched |
| status decided | INFO | slip_id, status, missing count, ERROR issue count, market |
| pipeline exception | ERROR | slip_id, exception with stack |
| approve / template version | INFO | slip_id, template_id, version, created? |
| audit write failure | ERROR | action, entity; request fails (no action without audit) |
| DB operational error | ERROR | operation; mapped to 503 |
| startup | INFO | settings summary (DB URL without password, thresholds, docs enabled) |

---

## 10. Assumptions register

Everything below is not fixed by the baseline and may be changed without contradicting it.

| # | Assumption |
|---|---|
| A1 | `app/errors.py` added for the shared exception hierarchy. |
| A2 | No `pydantic-settings`; `Settings.from_env()` reads `os.environ`. |
| A3 | Confidence / match scores / `market_confidence` are floats (not money, D10 not applicable). |
| A4 | Table-classification share threshold 0.8; grid composite `key_bbox` = header cell. |
| A5 | Fewer than 20 text characters ⇒ no text layer (`UNREADABLE`). |
| A6 | Fuzzy lookup only for normalised labels of ≥ 4 characters. |
| A7 | Candidate rank order region > regex > template > constant > synonym > abbreviation > fuzzy > text; conflicting same-rank values cap confidence at 0.85. |
| A8 | Market vote weights (3/3/3/2/2/2/1/1/1); the 0.70 cut-off itself is baseline. |
| A9 | Two-digit years pivot at 70; document-level date-order inference from unambiguous dates. |
| A10 | Price-precision tolerance `max(0.01, fv × 0.5·10^-s / 100)`; ACT/ACT accrued basis 365. |
| A11 | WARNING issues (holiday, accrued-days, T-Bill price, repo interest calc) do not block `PARSED`. |
| A12 | Template keywords act as a gate (all must be present). |
| A13 | (resolved in baseline) duplicates stored with `duplicate_of`; `/parse` audited as `SLIP_PARSED`, `entity_type=slip`, `entity_id=null`. |
| A14 | Problem type URNs `urn:bonds-parser:problem:<slug>`. |
| A15 | Extra indexes, `UNIQUE(prev_hash)`, template-version immutability trigger, no FK on `source_slip_id`. |
| A16 | Approve requires no missing required fields and a known market; forced reparse clears approval columns. |
| A17 | India holiday list shipped as a JSON data file in `app/engine/profiles/`. |
