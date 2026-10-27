from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError
from ulid import ULID

from acm_core.errors import InvalidIdError
from acm_core.ids import (
    AttemptId,
    CampaignId,
    PrefixedId,
    RunId,
    id_types,
    parse_id,
)

EXPECTED_PREFIXES = {
    "cmp", "bgt", "bdl", "wl", "cv", "hyp", "exp", "run", "att", "pa", "tgt", "ckpt",
    "art", "dec", "evd", "apr", "led", "ltx", "mc", "inc", "mem", "ksub",
}  # fmt: skip


def test_registered_prefixes() -> None:
    assert set(id_types()) == EXPECTED_PREFIXES


@pytest.mark.parametrize("id_type", list(id_types().values()), ids=lambda t: t.__name__)
def test_round_trip_every_type(id_type: type[PrefixedId]) -> None:
    new = id_type.new()
    assert new.startswith(f"{id_type.prefix}_")
    assert len(new) == len(id_type.prefix) + 1 + 26
    parsed = id_type(str(new))
    assert parsed == new
    assert type(parsed) is id_type
    assert type(parse_id(str(new))) is id_type
    assert id_type.parse(str(new)) == new
    assert id_type.is_valid(str(new))


def test_ids_are_strings_and_hash_like_strings() -> None:
    run_id = RunId.new()
    assert isinstance(run_id, str)
    lookup: dict[str, int] = {run_id: 1}
    assert lookup[str(run_id)] == 1
    assert repr(run_id) == f"RunId('{run_id}')"


def test_new_with_timestamp() -> None:
    at = datetime(2026, 10, 27, 12, 30, 15, 123000, tzinfo=UTC)
    run_id = RunId.new(at=at)
    assert run_id.created_at == at
    assert isinstance(run_id.ulid, ULID)


def test_ids_sort_by_creation_time() -> None:
    early = RunId.new(at=datetime(2026, 1, 1, tzinfo=UTC))
    late = RunId.new(at=datetime(2026, 6, 1, tzinfo=UTC))
    assert early < late


@pytest.mark.parametrize(
    "value",
    [
        "",
        "run_",
        "run",
        "cmp_01JB3V6Q1Z8K4M2N5P7R9S0T1W",  # wrong prefix
        "run_01jb3v6q1z8k4m2n5p7r9s0t1w",  # lowercase ULID is not canonical
        "RUN_01JB3V6Q1Z8K4M2N5P7R9S0T1W",
        "run-01JB3V6Q1Z8K4M2N5P7R9S0T1W",
        "run_01JB3V6Q1Z8K4M2N5P7R9S0T1",  # 25 chars
        "run_01JB3V6Q1Z8K4M2N5P7R9S0T1WX",  # 27 chars
        "run_01JB3V6Q1Z8K4M2N5P7R9S0T1U",  # U is not Crockford base32
        "run_81JB3V6Q1Z8K4M2N5P7R9S0T1W",  # timestamp overflows 128 bits
        " run_01JB3V6Q1Z8K4M2N5P7R9S0T1W",
    ],
)
def test_rejects_invalid(value: str) -> None:
    with pytest.raises(InvalidIdError) as exc:
        RunId(value)
    assert exc.value.code == "invalid_id"
    assert not RunId.is_valid(value)


def test_rejects_non_string() -> None:
    with pytest.raises(InvalidIdError):
        RunId(123)  # type: ignore[arg-type]


def test_base_class_is_abstract() -> None:
    with pytest.raises(TypeError):
        PrefixedId("run_01JB3V6Q1Z8K4M2N5P7R9S0T1W")


def test_parse_id_unknown_prefix() -> None:
    with pytest.raises(InvalidIdError) as exc:
        parse_id("zzz_01JB3V6Q1Z8K4M2N5P7R9S0T1W")
    assert "run" in exc.value.details["known_prefixes"]  # type: ignore[operator]
    with pytest.raises(InvalidIdError):
        parse_id("no-underscore")


def test_duplicate_prefix_rejected() -> None:
    with pytest.raises(ValueError, match="already used"):

        class _Dup(PrefixedId, prefix="run"):
            pass


def test_bad_prefix_rejected() -> None:
    with pytest.raises(ValueError, match="must match"):

        class _Bad(PrefixedId, prefix="Bad_"):
            pass


class _Model(BaseModel):
    run_id: RunId
    attempt_id: AttemptId | None = None


def test_pydantic_validates_and_serializes() -> None:
    run_id = RunId.new()
    model = _Model.model_validate({"run_id": str(run_id)})
    assert type(model.run_id) is RunId
    assert model.model_dump() == {"run_id": str(run_id), "attempt_id": None}
    assert type(model.model_dump()["run_id"]) is str
    assert _Model.model_validate_json(model.model_dump_json()) == model


def test_pydantic_rejects_wrong_prefix() -> None:
    with pytest.raises(ValidationError, match="invalid RunId"):
        _Model.model_validate({"run_id": str(CampaignId.new())})


def test_json_schema_has_pattern() -> None:
    schema = TypeAdapter(CampaignId).json_schema()
    assert schema["type"] == "string"
    assert schema["pattern"] == "^cmp_[0-7][0-9A-HJKMNP-TV-Z]{25}$"
