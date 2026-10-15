from __future__ import annotations

import pytest

from topskill.domain import RecordSchema, RecordSchemaIn
from topskill.errors import ApiError
from topskill.records import validate_schema
from topskill.records.schema import InvalidValue, normalize_value

from .helpers import VISA_SCHEMA, visa_schema

pytestmark = pytest.mark.unit


def _field(**kw):
    return {"key": "k", "label": "Label", "type": "text", **kw}


def _errors(raw) -> list[dict]:
    with pytest.raises(ApiError) as exc:
        validate_schema(raw)
    assert exc.value.status == 400 and exc.value.code == "record_schema_invalid"
    return exc.value.detail["errors"]


@pytest.mark.parametrize("raw", [None, {"fields": []}, RecordSchemaIn(fields=[]), {}])
def test_empty_means_disabled(raw) -> None:
    assert validate_schema(raw) is None


def test_valid_schema_normalised() -> None:
    schema = validate_schema(VISA_SCHEMA)
    assert isinstance(schema, RecordSchema)
    assert [f.key for f in schema.fields][:2] == ["nationality", "visa_types"]
    assert schema.fields[2].min == 0 and schema.fields[2].max == 100000
    assert validate_schema(schema) == schema  # idempotent, accepts the model itself


def test_drops_options_on_non_choice_and_range_on_non_number() -> None:
    schema = validate_schema(
        {
            "fields": [
                _field(options=[{"key": "a", "label": "A"}], min=1, max=2),
                _field(key="n", type="number"),
            ]
        }
    )
    assert schema.fields[0].options is None and schema.fields[0].min is None and schema.fields[0].max is None
    assert schema.fields[1].options is None


@pytest.mark.parametrize(
    ("raw_field", "loc_tail"),
    [
        (_field(key="Bad"), "key"),
        (_field(key="1abc"), "key"),
        (_field(key="a" * 41), "key"),
        (_field(label=""), "label"),
        (_field(label="x" * 41), "label"),
        (_field(description="x" * 301), "description"),
        (_field(type="date"), "type"),
        (_field(type="single_choice"), "options"),
        (_field(type="multi_choice", options=[]), "options"),
        (_field(type="single_choice", options=[{"key": "a", "label": f"{i}"} for i in range(31)]), "options"),
        (_field(type="single_choice", options=[{"key": "A", "label": "x"}]), "key"),
        (
            _field(type="single_choice", options=[{"key": "a", "label": "x"}, {"key": "a", "label": "y"}]),
            "key",
        ),
        (_field(type="single_choice", options=[{"key": "a", "label": ""}]), "label"),
        (_field(type="number", min=5, max=1), "min"),
        (_field(type="number", min="x"), "min"),
        (_field(required="false"), "required"),
        (_field(pii="no"), "pii"),
        (_field(show_in_table=1), "show_in_table"),
        (_field(label=123), "label"),
        (_field(description=["x"]), "description"),
        (_field(key=7), "key"),
        (_field(type="single_choice", options=[{"key": "a", "label": 5}]), "label"),
    ],
)
def test_strict_errors(raw_field, loc_tail) -> None:
    errors = _errors({"fields": [raw_field]})
    assert errors and errors[0]["loc"][-1] == loc_tail


def test_duplicate_keys_and_too_many_fields() -> None:
    errors = _errors({"fields": [_field(), _field()]})
    assert "duplicate" in errors[0]["msg"]
    errors = _errors({"fields": [_field(key=f"k{i}") for i in range(31)]})
    assert errors[0]["loc"] == ["fields"]


def test_lenient_drops_bad_fields() -> None:
    raw = {"fields": [_field(key="Bad"), _field(key="ok"), _field(key="ok"), _field(key="t", type="date")]}
    schema = validate_schema(raw, lenient=True)
    assert [f.key for f in schema.fields] == ["ok"]
    assert validate_schema({"fields": [_field(key="Bad")]}, lenient=True) is None
    many = validate_schema({"fields": [_field(key=f"k{i}") for i in range(35)]}, lenient=True)
    assert len(many.fields) == 30


def test_not_an_object() -> None:
    _errors("nope")  # type: ignore[arg-type]
    _errors({"fields": "x"})
    assert validate_schema("nope", lenient=True) is None  # type: ignore[arg-type]


def test_normalize_values() -> None:
    s = {f.key: f for f in visa_schema().fields}
    assert normalize_value(s["nationality"], "br") == "br"
    assert normalize_value(s["nationality"], "Brazil") == "br"  # label accepted
    assert normalize_value(s["visa_types"], ["work", "family", "work"]) == ["work", "family"]
    assert normalize_value(s["visa_types"], "study") == ["study"]
    assert normalize_value(s["visa_types"], []) is None
    assert normalize_value(s["budget_usd"], 12000.0) == 12000 and isinstance(
        normalize_value(s["budget_usd"], 1.0), int
    )
    assert normalize_value(s["budget_usd"], "1,500") == 1500
    assert normalize_value(s["has_offer"], "yes") is True
    assert normalize_value(s["full_name"], "  Ana  ") == "Ana"
    assert normalize_value(s["full_name"], "   ") is None
    assert len(normalize_value(s["full_name"], "x" * 900)) == 500
    assert len(normalize_value(s["notes"], "x" * 9000)) == 4000
    for field, bad in [
        ("nationality", "mars"),
        ("visa_types", ["work", "space"]),
        ("budget_usd", -1),
        ("budget_usd", 100001),
        ("budget_usd", True),
        ("budget_usd", "lots"),
        ("has_offer", "maybe"),
        ("full_name", {"a": 1}),
    ]:
        with pytest.raises(InvalidValue):
            normalize_value(s[field], bad)


def test_flags_only_real_booleans() -> None:
    schema = validate_schema({"fields": [_field(required=True, pii=False, show_in_table=None)]})
    assert schema is not None
    field = schema.fields[0]
    assert field.required is True and field.pii is False and field.show_in_table is False
