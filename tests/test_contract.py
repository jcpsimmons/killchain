"""The contract is the boundary: public_view() is the only outbound artefact.

If anything here fails, private data is leaving the machine.
"""
from __future__ import annotations

import json

import pytest

from killchain.contract import Contract, ContractError


def make(public_brief="Build a thing.",
         module="engine",
         function="run",
         params=None,
         public_fixtures=None,
         private_fixtures=None,
         private_notes="",
         private_plan=""):
    params = params if params is not None else [{"name": "x", "type": "int"}]
    public_fixtures = public_fixtures if public_fixtures is not None else [
        {"inputs": {"x": 1}, "output": 0}]
    private_fixtures = private_fixtures if private_fixtures is not None else [
        {"inputs": {"x": 7}, "output": 42}]
    return {
        "public_brief": public_brief,
        "interfaces": [{
            "module": module,
            "function": function,
            "description": "d",
            "params": params,
            "returns": "int",
            "fixtures": public_fixtures,
            "private_fixtures": private_fixtures,
        }],
        "private_notes": private_notes,
        "private_plan": private_plan,
    }


# --------------------------------------------------------------- leakage
def test_public_view_never_contains_private_fixtures():
    c = Contract.from_dict(make(private_fixtures=[{"inputs": {"x": 99}, "output": 12345}]))
    text = json.dumps(c.public_view())
    assert "12345" not in text
    assert "private_fixtures" not in text


def test_public_view_never_contains_private_notes_or_plan():
    c = Contract.from_dict(make(private_notes="SECRET-NOTES", private_plan="SECRET-PLAN"))
    text = json.dumps(c.public_view())
    assert "SECRET-NOTES" not in text
    assert "SECRET-PLAN" not in text


def test_public_text_matches_public_view():
    c = Contract.from_dict(make())
    assert json.loads(c.public_text()) == c.public_view()


def test_private_fixtures_dict_keeps_only_real_examples():
    c = Contract.from_dict(make(
        public_fixtures=[{"inputs": {"x": 1}, "output": 0}],
        private_fixtures=[{"inputs": {"x": 7}, "output": 42}]))
    d = c.private_fixtures_dict()
    assert d == {"engine": {"run": [{"inputs": {"x": 7}, "output": 42}]}}


# ------------------------------------------------------------ validation
def test_valid_contract_passes():
    Contract.from_dict(make()).validate()


def test_public_fixture_equal_to_private_is_rejected():
    same = [{"inputs": {"x": 5}, "output": 1}]
    c = Contract.from_dict(make(public_fixtures=same, private_fixtures=same))
    with pytest.raises(ContractError, match="equals a private fixture"):
        c.validate()


def test_identical_fixture_detected_regardless_of_key_order():
    c = Contract.from_dict(make(
        public_fixtures=[{"inputs": {"x": 5}, "output": 1}],
        private_fixtures=[{"output": 1, "inputs": {"x": 5}}]))
    with pytest.raises(ContractError, match="equals a private fixture"):
        c.validate()


def test_missing_public_fixtures_rejected():
    c = Contract.from_dict(make(public_fixtures=[]))
    with pytest.raises(ContractError, match="no public fixtures"):
        c.validate()


def test_missing_private_fixtures_rejected():
    c = Contract.from_dict(make(private_fixtures=[]))
    with pytest.raises(ContractError, match="no private_fixtures"):
        c.validate()


def test_empty_brief_rejected():
    with pytest.raises(ContractError, match="public_brief is empty"):
        Contract.from_dict(make(public_brief="   ")).validate()


def test_no_interfaces_rejected():
    c = Contract.from_dict({"public_brief": "x", "interfaces": []})
    with pytest.raises(ContractError, match="no interfaces"):
        c.validate()


def test_bad_identifier_rejected():
    with pytest.raises(ContractError, match="snake_case"):
        Contract.from_dict(make(function="Run-It")).validate()


def test_duplicate_interface_rejected():
    d = make()
    with pytest.raises(ContractError, match="duplicate interface"):
        Contract.from_dict({**d, "interfaces": d["interfaces"] * 2}).validate()


def test_fixture_inputs_must_match_params():
    c = Contract.from_dict(make(
        params=[{"name": "x", "type": "int"}],
        public_fixtures=[{"inputs": {"y": 1}, "output": 0}]))
    with pytest.raises(ContractError, match="inputs mismatch params"):
        c.validate()


def test_non_json_fixture_rejected():
    c = Contract.from_dict(make(
        public_fixtures=[{"inputs": {"x": object()}, "output": 0}]))
    with pytest.raises(ContractError, match="not JSON"):
        c.validate()


def test_malformed_dict_raises_contract_error_not_keyerror():
    with pytest.raises(ContractError, match="malformed contract"):
        Contract.from_dict({"interfaces": [{"module": "m"}]})


def test_roundtrip_to_dict_and_back():
    c = Contract.from_dict(make())
    assert Contract.from_dict(c.to_dict()).public_view() == c.public_view()
