"""Contract: the agreed interface between the public (frontier-built) half and
the private (abliterated-model-built) half of a system.

The private half exists because the frontier may refuse, hedge, simplify, or
guess at the risky parts. The contract is produced by the LOCAL model from the
user's full prompt. Only `Contract.public_view()` ever leaves the machine;
`private_notes` and the original prompt never do.

Public fixtures are decoys on purpose: shape without behaviour. A handful of
correct input/output points is enough for a reader to fit a rule, and a
reverse-engineered rule is one the frontier no longer needs to have been told.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any

IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class ContractError(ValueError):
    pass


@dataclass
class Param:
    name: str
    type: str
    description: str = ""


@dataclass
class Fixture:
    inputs: dict[str, Any]
    output: Any
    note: str = ""


@dataclass
class Interface:
    module: str
    function: str
    description: str
    params: list[Param]
    returns: str
    fixtures: list[Fixture]                                  # PUBLIC decoys: shape only
    private_fixtures: list[Fixture] = field(default_factory=list)  # real behaviour, never leaves


@dataclass
class Contract:
    public_brief: str
    interfaces: list[Interface]
    private_notes: str = ""  # NEVER leaves the machine
    private_plan: str = ""   # NEVER leaves the machine

    # ---- construction -------------------------------------------------
    @staticmethod
    def from_dict(d: dict) -> "Contract":
        try:
            ifaces = []
            for i in d.get("interfaces", []):
                params = [Param(**p) for p in i.get("params", [])]
                fixtures = [Fixture(**f) for f in i.get("fixtures", [])]
                pfix = [Fixture(**f) for f in i.get("private_fixtures", [])]
                ifaces.append(Interface(
                    module=i["module"], function=i["function"],
                    description=i.get("description", ""),
                    params=params, returns=i.get("returns", "Any"),
                    fixtures=fixtures, private_fixtures=pfix,
                ))
            return Contract(
                public_brief=d["public_brief"], interfaces=ifaces,
                private_notes=d.get("private_notes", ""),
                private_plan=d.get("private_plan", ""),
            )
        except (KeyError, TypeError) as e:
            raise ContractError(f"malformed contract: {e}") from e

    @staticmethod
    def from_json(s: str) -> "Contract":
        return Contract.from_dict(json.loads(s))

    # ---- validation ---------------------------------------------------
    def validate(self) -> None:
        if not self.public_brief.strip():
            raise ContractError("public_brief is empty")
        if not self.interfaces:
            raise ContractError("contract has no interfaces; nothing is risky, "
                                "so there is nothing to partition")
        seen = set()
        for i in self.interfaces:
            for ident in (i.module, i.function):
                if not IDENT.match(ident):
                    raise ContractError(f"bad identifier {ident!r} (snake_case only)")
            key = (i.module, i.function)
            if key in seen:
                raise ContractError(f"duplicate interface {i.module}.{i.function}")
            seen.add(key)
            if not i.fixtures:
                raise ContractError(f"{i.module}.{i.function} has no public fixtures; "
                                    "every interface needs at least one synthetic decoy example")
            if not i.private_fixtures:
                raise ContractError(f"{i.module}.{i.function} has no private_fixtures; "
                                    "every interface needs at least one real example for conformance")
            pnames = {p.name for p in i.params}
            for p in i.params:
                if not IDENT.match(p.name):
                    raise ContractError(f"bad param name {p.name!r} in {i.function}")
            for kind, fixtures in (("fixture", i.fixtures), ("private_fixture", i.private_fixtures)):
                for n, f in enumerate(fixtures):
                    extra = set(f.inputs) - pnames
                    missing = pnames - set(f.inputs)
                    if extra or missing:
                        raise ContractError(
                            f"{kind} {n} of {i.module}.{i.function} inputs mismatch params: "
                            f"extra={sorted(extra)} missing={sorted(missing)}")
                    try:
                        json.dumps(f.inputs); json.dumps(f.output)
                    except TypeError as e:
                        raise ContractError(f"{kind} {n} of {i.function} not JSON: {e}")
            real = {json.dumps([f.inputs, f.output], sort_keys=True) for f in i.private_fixtures}
            for n, f in enumerate(i.fixtures):
                if json.dumps([f.inputs, f.output], sort_keys=True) in real:
                    raise ContractError(
                        f"public fixture {n} of {i.module}.{i.function} equals a private fixture; "
                        "public fixtures must be decoys, not real behaviour")

    # ---- views --------------------------------------------------------
    def public_view(self) -> dict:
        """Everything the frontier model is allowed to see. private_fixtures excluded."""
        ifaces = []
        for i in self.interfaces:
            d = asdict(i)
            d.pop("private_fixtures", None)
            ifaces.append(d)
        return {"public_brief": self.public_brief, "interfaces": ifaces}

    def private_fixtures_dict(self) -> dict:
        """{module: {function: [{inputs, output}]}} from the REAL examples."""
        out: dict = {}
        for i in self.interfaces:
            out.setdefault(i.module, {})[i.function] = [
                {"inputs": f.inputs, "output": f.output} for f in i.private_fixtures]
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    def public_text(self) -> str:
        """Flattened outbound text, for the redaction gate."""
        return json.dumps(self.public_view(), indent=2, sort_keys=True)
