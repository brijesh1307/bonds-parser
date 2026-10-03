"""Template onboarding: preview and approve with the PDF in the request; edit, import (baseline §7).

Nothing from the slip is stored: the PDF is parsed in memory, only the template (labels and
rules) is written to the template store.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from app.config import Settings
from app.engine.detect import fingerprint
from app.engine.fields import (
    IGNORE,
    Market,
    Method,
    Severity,
    is_valid_field_path,
    normalise_label,
    required_fields,
)
from app.engine.models import (
    EngineResult,
    ExtractedDocument,
    MarketDetection,
    RegexRule,
    RegionRule,
    TemplateDefinition,
)
from app.engine.pipeline import run
from app.errors import (
    ApprovalBlockedError,
    ConflictError,
    MappingValidationError,
    ValidationNotAcceptedError,
)
from app.schemas.template import (
    MappingSpec,
    TemplateDefinitionModel,
    TemplateHistoryItem,
    TemplateImport,
    TemplateOut,
    TemplatePatch,
    TemplateVersionIn,
    TemplateVersionOut,
)
from app.services.template_store import TemplateStore

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,99}$")


# --------------------------------------------------------------------------- validation


def validate_spec(spec: MappingSpec) -> None:
    """Field paths, regexes and boxes must be valid -> 400 invalid-mapping with one entry per problem."""
    errors: list[dict[str, Any]] = []
    for label, target in spec.label_map.items():
        if target != IGNORE and not is_valid_field_path(target):
            errors.append({"loc": ["label_map", label], "msg": f"unknown field path {target!r}"})
    for i, r in enumerate(spec.region_rules):
        if not is_valid_field_path(r.field):
            errors.append({"loc": ["region_rules", i, "field"], "msg": f"unknown field path {r.field!r}"})
        x0, top, x1, bottom = r.bbox
        if not (x0 < x1 and top < bottom):
            errors.append(
                {
                    "loc": ["region_rules", i, "bbox"],
                    "msg": "bbox must be [x0, top, x1, bottom] with x0<x1, top<bottom",
                }
            )
    for i, rx in enumerate(spec.regex_rules):
        if not is_valid_field_path(rx.field):
            errors.append({"loc": ["regex_rules", i, "field"], "msg": f"unknown field path {rx.field!r}"})
        try:
            compiled = re.compile(rx.pattern)
            if rx.group > compiled.groups:
                errors.append(
                    {"loc": ["regex_rules", i, "group"], "msg": f"pattern has {compiled.groups} group(s)"}
                )
        except re.error as exc:
            errors.append({"loc": ["regex_rules", i, "pattern"], "msg": f"invalid regex: {exc}"})
    for path in spec.constants:
        if not is_valid_field_path(path):
            errors.append({"loc": ["constants", path], "msg": f"unknown field path {path!r}"})
    if errors:
        raise MappingValidationError(f"{len(errors)} problem(s) in the mapping", errors=errors)


# --------------------------------------------------------------------------- draft template


class DraftBuilder:
    """Builds the draft template from the extracted slip, the reviewer's mapping and a base version."""

    def __init__(
        self,
        spec: MappingSpec,
        *,
        template_id: str,
        version: int,
        base: TemplateDefinition | None,
        default_threshold: float,
        name: str | None,
    ) -> None:
        self.spec, self.template_id, self.version, self.base = spec, template_id, version, base
        self.default_threshold, self.name = default_threshold, name
        self.built: TemplateDefinition | None = None

    def __call__(self, doc: ExtractedDocument, market: MarketDetection) -> TemplateDefinition:
        spec, base = self.spec, self.base
        label_map = dict(base.label_map) if base else {}
        ignore = set(base.ignore) if base else set()
        for raw_label, target in spec.label_map.items():
            key = normalise_label(raw_label)[0]
            if target == IGNORE:
                ignore.add(key)
                label_map.pop(key, None)
            else:
                label_map[key] = target
                ignore.discard(key)
        detected = market.market if market.market != Market.UNKNOWN else None
        self.built = TemplateDefinition(
            template_id=self.template_id,
            version=self.version,
            market=detected or (base.market if base else Market.UNKNOWN),
            fingerprint_labels=fingerprint(doc),
            keywords=tuple(spec.keywords) or (base.keywords if base else ()),
            match_threshold=base.match_threshold if base else self.default_threshold,
            label_map=label_map,
            region_rules=tuple(
                RegionRule(r.field, r.page, (r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3]))
                for r in spec.region_rules
            )
            or (base.region_rules if base else ()),
            regex_rules=tuple(RegexRule(r.field, r.pattern, r.group) for r in spec.regex_rules)
            or (base.regex_rules if base else ()),
            constants=dict(spec.constants) or (dict(base.constants) if base else {}),
            ignore=frozenset(ignore),
            accepted_derived=base.accepted_derived if base else frozenset(),
            date_order=spec.date_order or (base.date_order if base else None),
            name=self.name,
        )
        return self.built


def _target(
    store: TemplateStore, spec: MappingSpec, settings: Settings, *, for_save: bool
) -> tuple[DraftBuilder, dict[str, Any] | None]:
    record = store.get(spec.template_id) if spec.template_id else None
    if record:
        base = TemplateDefinition.from_json(store.current(record)["definition"], name=record["name"])
        template_id, version, name = record["id"], record["current_version"] + 1, record["name"]
    else:
        if for_save and not spec.template_name:
            raise MappingValidationError(
                "template_name is required for a new template",
                errors=[{"loc": ["template_name"], "msg": "required"}],
            )
        base, version = None, 1
        name = spec.template_name or "draft"
        template_id = store.new_id(name) if for_save else "draft"
    builder = DraftBuilder(
        spec,
        template_id=template_id,
        version=version,
        base=base,
        default_threshold=settings.template_match_threshold,
        name=name,
    )
    return builder, record


# --------------------------------------------------------------------------- operations


def preview(
    data: bytes, file_name: str, sha256: str, spec: MappingSpec, store: TemplateStore, settings: Settings
) -> EngineResult:
    validate_spec(spec)
    builder, _ = _target(store, spec, settings, for_save=False)
    return run(data, file_name=file_name, sha256=sha256, es=settings.engine, draft=builder)


def approve(
    data: bytes,
    file_name: str,
    sha256: str,
    spec: MappingSpec,
    store: TemplateStore,
    settings: Settings,
    actor: str,
) -> tuple[dict[str, Any], EngineResult, bool]:
    """Save the mapping as a template (or a new version). Returns (record, result, created)."""
    validate_spec(spec)
    builder, record = _target(store, spec, settings, for_save=True)
    draft_result = run(data, file_name=file_name, sha256=sha256, es=settings.engine, draft=builder)

    if draft_result.market.market == Market.UNKNOWN:
        raise ApprovalBlockedError(
            "the market of this slip could not be determined", errors=[{"field": "market", "msg": "UNKNOWN"}]
        )
    if draft_result.missing_required:
        raise ApprovalBlockedError(
            f"required fields still missing: {', '.join(draft_result.missing_required)}",
            errors=[{"field": f, "msg": "missing"} for f in draft_result.missing_required],
        )
    errors = [i for i in draft_result.validation if i.severity is Severity.ERROR]
    if errors and not spec.accept_validation_issues:
        raise ValidationNotAcceptedError(
            "validation issues must be fixed or accepted (accept_validation_issues=true)",
            errors=[{"code": i.code, "msg": i.message, "fields": list(i.fields)} for i in errors],
        )

    assert builder.built is not None  # noqa: S101 - set by the draft run above
    weak = {
        p
        for p in required_fields(draft_result.deal.get("deal_type"))
        if p in draft_result.fields
        and draft_result.fields[p].method in (Method.derived, Method.text, Method.fuzzy)
    }
    definition = dataclasses.replace(builder.built, accepted_derived=builder.built.accepted_derived | weak)

    if record:
        saved = store.add_version(record["id"], definition, actor=actor, note=spec.note)
    else:
        saved = store.create(
            template_id=definition.template_id,
            name=builder.name or definition.template_id,
            description=spec.description,
            definition=definition,
            actor=actor,
            note=spec.note,
        )
    final = run(
        data,
        file_name=file_name,
        sha256=sha256,
        es=settings.engine,
        templates=[TemplateDefinition.from_json(definition.to_json(), name=saved["name"])],
    )
    return saved, final, record is None


def _definition_from_model(
    model: TemplateDefinitionModel, *, template_id: str, version: int
) -> TemplateDefinition:
    data = model.model_dump()
    data.update(template_id=template_id, version=version)
    spec = MappingSpec(
        label_map=data["label_map"],
        region_rules=model.region_rules,
        regex_rules=model.regex_rules,
        constants=data["constants"],
    )
    validate_spec(spec)
    return TemplateDefinition.from_json(data)


def put_version(
    store: TemplateStore, template_id: str, body: TemplateVersionIn, actor: str
) -> dict[str, Any]:
    record = store.get(template_id)
    definition = _definition_from_model(
        body.definition, template_id=template_id, version=record["current_version"] + 1
    )
    return store.add_version(template_id, definition, actor=actor, note=body.note)


def patch(
    store: TemplateStore, template_id: str, body: TemplatePatch
) -> tuple[dict[str, Any], dict[str, Any]]:
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise MappingValidationError(
            "nothing to change", errors=[{"msg": "send name, description or is_active"}]
        )
    return store.patch(template_id, changes)


def import_template(store: TemplateStore, body: TemplateImport, actor: str) -> dict[str, Any]:
    template_id = body.definition.template_id
    if not _ID_RE.match(template_id):
        raise MappingValidationError(
            "invalid template_id",
            errors=[{"loc": ["definition", "template_id"], "msg": "use a-z, 0-9 and _"}],
        )
    if store.exists(template_id):
        raise ConflictError(f"template {template_id!r} already exists; add a version with PUT instead")
    definition = _definition_from_model(body.definition, template_id=template_id, version=1)
    return store.create(
        template_id=template_id,
        name=body.name,
        description=body.description,
        definition=definition,
        actor=actor,
        note="imported",
    )


_MAP_KEYS = ("label_map", "constants")
_SET_KEYS = ("ignore", "accepted_derived")
_RULE_KEYS = ("region_rules", "regex_rules")
_SCALAR_KEYS = ("market", "match_threshold", "date_order")


def diff_definitions(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What changed between two template definitions (only keys that changed are returned)."""
    out: dict[str, Any] = {}
    for key in _MAP_KEYS:
        a, b = old.get(key) or {}, new.get(key) or {}
        d = {
            "added": {k: b[k] for k in sorted(b.keys() - a.keys())},
            "removed": {k: a[k] for k in sorted(a.keys() - b.keys())},
            "changed": {k: {"old": a[k], "new": b[k]} for k in sorted(a.keys() & b.keys()) if a[k] != b[k]},
        }
        if any(d.values()):
            out[key] = d
    pairs = [(k, (old.get(k) or []), (new.get(k) or [])) for k in _SET_KEYS]
    pairs += [
        (
            "fingerprint.labels",
            (old.get("fingerprint") or {}).get("labels") or [],
            (new.get("fingerprint") or {}).get("labels") or [],
        ),
        (
            "fingerprint.keywords",
            (old.get("fingerprint") or {}).get("keywords") or [],
            (new.get("fingerprint") or {}).get("keywords") or [],
        ),
    ]
    for key, a_list, b_list in pairs:
        a_set, b_set = set(a_list), set(b_list)
        if a_set != b_set:
            out[key] = {"added": sorted(b_set - a_set), "removed": sorted(a_set - b_set)}
    for key in _RULE_KEYS:
        if (old.get(key) or []) != (new.get(key) or []):
            out[key] = {"old": old.get(key) or [], "new": new.get(key) or []}
    for key in _SCALAR_KEYS:
        if old.get(key) != new.get(key):
            out[key] = {"old": old.get(key), "new": new.get(key)}
    return out


def history(record: dict[str, Any]) -> list[TemplateHistoryItem]:
    items: list[TemplateHistoryItem] = []
    previous: dict[str, Any] | None = None
    for v in sorted(record["versions"], key=lambda v: v["version"]):
        changes: dict[str, Any] | None = (
            None if previous is None else diff_definitions(previous["definition"], v["definition"])
        )
        items.append(
            TemplateHistoryItem(
                version=v["version"],
                created_by=v["created_by"],
                created_at=v["created_at"],
                note=v.get("note"),
                changes=changes,
            )
        )
        previous = v
    return items


def to_out(record: dict[str, Any], *, versions: bool = False) -> TemplateOut:
    current = TemplateStore.current(record)
    return TemplateOut(
        id=record["id"],
        name=record["name"],
        description=record.get("description"),
        market=record["market"],
        is_active=record["is_active"],
        current_version=record["current_version"],
        created_by=record["created_by"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        definition=TemplateDefinitionModel.model_validate(current["definition"]),
        versions=[
            TemplateVersionOut(
                version=v["version"],
                definition=TemplateDefinitionModel.model_validate(v["definition"]),
                note=v.get("note"),
                created_by=v["created_by"],
                created_at=v["created_at"],
            )
            for v in record["versions"]
        ]
        if versions
        else None,
    )
