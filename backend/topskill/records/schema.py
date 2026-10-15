"""Record schema validation and value normalisation (SPEC §10.3).

``validate_schema`` turns a creator-supplied schema (wire model ``RecordSchemaIn`` or a raw dict) into the
stored ``RecordSchema``, or ``None`` when records are disabled. ``normalize_value`` is the single place
that checks one value against one field; ``apply_values`` and the type-lock check both use it.
"""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import ValidationError

from topskill.domain import RecordField, RecordOption, RecordSchema, RecordSchemaIn
from topskill.errors import ApiError, Codes

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_FIELDS = 30
MAX_OPTIONS = 30
MAX_LABEL = 40
MAX_DESCRIPTION = 300
FIELD_TYPES = ("single_choice", "multi_choice", "number", "boolean", "text", "long_text")
CHOICE_TYPES = frozenset({"single_choice", "multi_choice"})
TEXT_LIMITS = {"text": 500, "long_text": 4000}
EVIDENCE_LIMIT = 500
SUMMARY_LIMIT = 500

_TRUE = frozenset({"true", "yes", "y", "1"})
_FALSE = frozenset({"false", "no", "n", "0"})

RecordScalar = str | int | float | bool | list[str] | None


class FieldErrors:
    """Collects ``{"loc": [...], "msg": "..."}`` entries (same shape as ``validation_error``)."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, loc: list[str | int], msg: str) -> None:
        self.items.append({"loc": [str(p) for p in loc], "msg": msg})

    def __bool__(self) -> bool:
        return bool(self.items)


def _invalid(errors: list[dict[str, Any]]) -> ApiError:
    return ApiError(400, Codes.RECORD_SCHEMA_INVALID, "The record fields are invalid.", {"errors": errors})


def _str_or_empty(value: Any) -> str:
    """Strings pass through; anything else (already reported as an error) becomes ``""``."""
    return value if isinstance(value, str) else ""


def _clean_text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _normalize_field(raw: Any, index: int, errors: FieldErrors) -> RecordField | None:
    """One field → normalised ``RecordField``; problems are added to ``errors`` (and ``None`` returned)."""
    loc: list[str | int] = ["fields", index]
    if isinstance(raw, RecordField):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        errors.add(loc, "must be an object")
        return None
    before = len(errors.items)
    for name in ("key", "label", "description"):
        if raw.get(name) is not None and not isinstance(raw.get(name), str):
            errors.add([*loc, name], "must be a string")
    flags: dict[str, bool] = {}
    for name in ("required", "pii", "show_in_table"):
        flag = raw.get(name)
        if flag is not None and not isinstance(flag, bool):
            errors.add([*loc, name], "must be true or false")
        flags[name] = flag is True
    key = _str_or_empty(raw.get("key")).strip()
    if not KEY_RE.match(key):
        errors.add([*loc, "key"], "must match ^[a-z][a-z0-9_]{0,39}$")
    label = _clean_text(_str_or_empty(raw.get("label")))
    if not label:
        errors.add([*loc, "label"], "is required")
    elif len(label) > MAX_LABEL:
        errors.add([*loc, "label"], f"must be at most {MAX_LABEL} characters")
    description = _str_or_empty(raw.get("description")).strip()
    if len(description) > MAX_DESCRIPTION:
        errors.add([*loc, "description"], f"must be at most {MAX_DESCRIPTION} characters")
    ftype = raw.get("type")
    if ftype not in {"single_choice", "multi_choice", "number", "boolean", "text", "long_text"}:
        errors.add(
            [*loc, "type"], "must be one of single_choice, multi_choice, number, boolean, text, long_text"
        )

    options: list[RecordOption] | None = None
    if ftype in CHOICE_TYPES:
        raw_options = raw.get("options") or []
        if not isinstance(raw_options, list) or not 1 <= len(raw_options) <= MAX_OPTIONS:
            errors.add([*loc, "options"], f"choice fields need 1 to {MAX_OPTIONS} options")
            raw_options = raw_options if isinstance(raw_options, list) else []
        options = []
        seen: set[str] = set()
        for j, opt in enumerate(raw_options):
            if isinstance(opt, RecordOption):
                opt = opt.model_dump()
            if not isinstance(opt, dict):
                errors.add([*loc, "options", j], "must be an object")
                continue
            if not isinstance(opt.get("key"), str):
                errors.add([*loc, "options", j, "key"], "must be a string")
            if not isinstance(opt.get("label"), str):
                errors.add([*loc, "options", j, "label"], "must be a string")
            okey = _str_or_empty(opt.get("key")).strip()
            olabel = _clean_text(_str_or_empty(opt.get("label")))
            if not KEY_RE.match(okey):
                errors.add([*loc, "options", j, "key"], "must match ^[a-z][a-z0-9_]{0,39}$")
            elif okey in seen:
                errors.add([*loc, "options", j, "key"], f"duplicate option key '{okey}'")
            if not olabel or len(olabel) > MAX_LABEL:
                errors.add([*loc, "options", j, "label"], f"must be 1 to {MAX_LABEL} characters")
            seen.add(okey)
            options.append(RecordOption.model_construct(key=okey, label=olabel))

    fmin = fmax = None
    if ftype == "number":
        fmin, fmax = raw.get("min"), raw.get("max")
        for name, bound in (("min", fmin), ("max", fmax)):
            if bound is not None and (
                isinstance(bound, bool) or not isinstance(bound, int | float) or not math.isfinite(bound)
            ):
                errors.add([*loc, name], "must be a number")
        if (
            isinstance(fmin, int | float)
            and isinstance(fmax, int | float)
            and not isinstance(fmin, bool)
            and not isinstance(fmax, bool)
            and fmin > fmax
        ):
            errors.add([*loc, "min"], "min must not be greater than max")

    if len(errors.items) > before:
        return None
    return RecordField(
        key=key,
        label=label,
        type=ftype,
        description=description,
        options=options,  # dropped silently for non-choice types
        required=flags["required"],
        pii=flags["pii"],
        show_in_table=flags["show_in_table"],
        min=float(fmin) if fmin is not None else None,  # dropped silently for non-number types
        max=float(fmax) if fmax is not None else None,
    )


def validate_schema(raw: RecordSchemaIn | dict | None, *, lenient: bool = False) -> RecordSchema | None:
    """Normalise a record schema; ``None`` / ``fields: []`` → ``None`` (records disabled).

    Strict mode raises 400 ``record_schema_invalid`` with ``detail.errors``; lenient mode (used by the
    draft) drops every invalid or duplicate field and keeps the first 30 valid ones.
    """
    if raw is None:
        return None
    if isinstance(raw, RecordSchemaIn):
        data: dict[str, Any] = raw.model_dump()
    elif isinstance(raw, dict):
        data = raw
    else:
        if lenient:
            return None
        raise _invalid([{"loc": ["record_schema"], "msg": "must be an object"}])

    errors = FieldErrors()
    if data.get("version", 1) != 1:
        errors.add(["version"], "must be 1")
    if data.get("scope", "session") != "session":
        errors.add(["scope"], "must be 'session'")
    raw_fields = data.get("fields")
    if raw_fields is None:
        raw_fields = []
    if not isinstance(raw_fields, list):
        if lenient:
            return None
        raise _invalid([{"loc": ["fields"], "msg": "must be a list"}])
    if len(raw_fields) > MAX_FIELDS and not lenient:
        errors.add(["fields"], f"at most {MAX_FIELDS} fields")

    fields: list[RecordField] = []
    seen: set[str] = set()
    for i, raw_field in enumerate(raw_fields):
        field_errors = FieldErrors()
        field = _normalize_field(raw_field, i, field_errors)
        if field is not None and field.key in seen:
            field_errors.add(["fields", i, "key"], f"duplicate key '{field.key}'")
            field = None
        if field is None:
            errors.items.extend(field_errors.items)
            continue
        seen.add(field.key)
        fields.append(field)

    if lenient:
        fields = fields[:MAX_FIELDS]
    elif errors:
        raise _invalid(errors.items)
    if not fields:
        return None
    try:
        return RecordSchema(fields=fields)
    except ValidationError as exc:  # defensive: the checks above mirror the model constraints
        if lenient:
            return None
        raise _invalid(
            [{"loc": [str(p) for p in e["loc"]], "msg": str(e["msg"])} for e in exc.errors()]
        ) from exc


# --------------------------------------------------------------------------- values


class InvalidValue(Exception):
    """A value that does not fit its field; ``str(exc)`` tells the model what to fix."""


def _option_key(field: RecordField, raw: Any) -> str:
    options = field.options or []
    if isinstance(raw, str):
        text = raw.strip()
        for opt in options:
            if text == opt.key:
                return opt.key
        lowered = text.lower()
        for opt in options:
            if lowered in (opt.key.lower(), opt.label.lower()):
                return opt.key
    allowed = ", ".join(o.key for o in options)
    raise InvalidValue(f"'{field.key}' must be one of: {allowed}")


def _number(field: RecordField, raw: Any) -> int | float:
    if isinstance(raw, bool):
        raise InvalidValue(f"'{field.key}' must be a number")
    if isinstance(raw, str):
        try:
            raw = float(raw.strip().replace(",", ""))
        except ValueError:
            raise InvalidValue(f"'{field.key}' must be a number") from None
    if not isinstance(raw, int | float) or not math.isfinite(raw):
        raise InvalidValue(f"'{field.key}' must be a number")
    if field.min is not None and raw < field.min:
        raise InvalidValue(f"'{field.key}' must be at least {_fmt_num(field.min)}")
    if field.max is not None and raw > field.max:
        raise InvalidValue(f"'{field.key}' must be at most {_fmt_num(field.max)}")
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return raw


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def normalize_value(field: RecordField, raw: Any) -> RecordScalar:
    """Validate and normalise one value for ``field``; ``None`` means "clear". Raises ``InvalidValue``."""
    if raw is None:
        return None
    match field.type:
        case "single_choice":
            return _option_key(field, raw)
        case "multi_choice":
            items = raw if isinstance(raw, list) else [raw]
            keys: list[str] = []
            for item in items:
                key = _option_key(field, item)
                if key not in keys:
                    keys.append(key)
            return keys or None
        case "number":
            return _number(field, raw)
        case "boolean":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.strip().lower() in _TRUE | _FALSE:
                return raw.strip().lower() in _TRUE
            raise InvalidValue(f"'{field.key}' must be true or false")
        case _:  # text / long_text
            if isinstance(raw, bool) or not isinstance(raw, str | int | float):
                raise InvalidValue(f"'{field.key}' must be text")
            text = str(raw).strip()
            if not text:
                return None
            return text[: TEXT_LIMITS[field.type]]


def value_fits(field: RecordField, value: Any) -> bool:
    """Whether a stored value is still valid for ``field`` (used by the type-lock check)."""
    try:
        return normalize_value(field, value) == value or value is None
    except InvalidValue:
        return False


def format_range(field: RecordField) -> str:
    if field.min is None and field.max is None:
        return ""
    low = _fmt_num(field.min) if field.min is not None else ""
    high = _fmt_num(field.max) if field.max is not None else ""
    return f"{low}–{high}"
