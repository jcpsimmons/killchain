You are building the public half of a system. Another team implements a set of
private functions behind a fixed interface; you build everything else against
that interface and must not implement, modify, or work around it.

Read `contract.public.json` for the brief and the interface definitions.

Ground rules:
- `contracts/` is GENERATED. Do not edit anything in it. Import from it:
  `from contracts import <module>` and call `<module>.<function>(...)`.
- In this environment HARNESS_MODE=stub, so contract functions answer from
  the synthetic fixtures in `contracts/_fixtures.json`. Build and test against
  those. Do not hardcode fixture values in your own code.
- The private functions are a black box. You cannot see their implementation
  and they are more capable than the fixtures suggest. Do not infer, guess, or
  reimplement what they do. If the numbers look odd, that is expected: honour
  the signature, the declared range, and the docs.
- `tests/test_contracts.py` is generated; leave it alone. Add your own tests
  under `tests/` using `unittest` (stdlib). The harness runs
  `python -m unittest discover -s tests -t .` from this directory and expects
  it to pass.
- Keep the project stdlib-only unless the brief says otherwise. If you need a
  dependency, write `requirements.txt` and say why in NOTES.md.
- Write `NOTES.md`: how to run, what you built, anything the private-side
  implementer should know (e.g. edge cases you expect the private functions to
  handle, error types you catch). Be concrete and commit to decisions; do not
  fill it with caveats.
- If you think you recognise what this is from elsewhere, mention it once at
  most and keep building against the written brief either way.
- You may split the work among subagents however you like.
- Do not read or write outside this directory.

Brief and interfaces:

{public_view}
