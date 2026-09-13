"""Metadata handed over as a JSON string must be accepted, not rejected.

Observed failure this guards against: a reviewer worker built a full structured
handoff, called kanban_complete twice, and both times got
``metadata must be an object/dict, got str`` because its tool-call argument was
serialised as text. It then completed the card with ``{"test": "hello"}`` to get
past the type check, so the real handoff was lost and the card still went to
``done`` — a false success with no way for the worker to comply.

The CLI's ``--metadata`` flag already accepts JSON text, so the tool path
accepting it too makes the two entry points agree.
"""

import pytest

from tools.kanban_tools import _coerce_metadata_json, _require_dict_metadata


def test_dict_passes_through_unchanged():
    payload = {"verdict": "changes_requested", "findings": [1, 2]}
    assert _coerce_metadata_json(payload) is payload


def test_none_passes_through():
    assert _coerce_metadata_json(None) is None


def test_json_object_string_is_parsed():
    result = _coerce_metadata_json('{"verdict": "approved", "findings": []}')
    assert result == {"verdict": "approved", "findings": []}
    _require_dict_metadata(result)  # must not raise


def test_nested_structure_survives_round_trip():
    raw = '{"scope": {"base": "abc123"}, "findings": [{"severity": "high"}]}'
    result = _coerce_metadata_json(raw)
    assert result["scope"]["base"] == "abc123"
    assert result["findings"][0]["severity"] == "high"


def test_empty_string_is_treated_as_absent():
    assert _coerce_metadata_json("   ") is None


def test_unparseable_string_is_rejected_loudly():
    with pytest.raises(Exception) as exc:
        _coerce_metadata_json("{not valid json")
    assert "metadata" in str(exc.value)


def test_json_array_string_still_fails_type_check():
    """Parsing must not smuggle a non-object past the dict requirement."""
    parsed = _coerce_metadata_json('["a", "b"]')
    with pytest.raises(Exception) as exc:
        _require_dict_metadata(parsed)
    assert "object/dict" in str(exc.value)


def test_json_scalar_string_still_fails_type_check():
    parsed = _coerce_metadata_json("42")
    with pytest.raises(Exception) as exc:
        _require_dict_metadata(parsed)
    assert "object/dict" in str(exc.value)
