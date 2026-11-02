You are implementing the private half of a system on the local machine. The
public half has already been built by a third party against the interfaces in
the contract below. Your code must satisfy those interfaces exactly, including
the fixtures: in real mode the contract tests call your functions with each
fixture's inputs and compare to its output.

Write one Python module per contract module at `private_impl/<module>.py`,
exposing each function with the exact name and keyword parameters. Stdlib
only unless private_notes says otherwise. You may add helper modules under
`private_impl/`.

Reply with ONE JSON object, no prose:

{{
  "files": {{
    "private_impl/__init__.py": "",
    "private_impl/<module>.py": "<full file contents>"
  }},
  "notes": "anything the operator should know"
}}

Original user request (confidential, local only):
{prompt}

Full contract (including private_notes and private_plan):
{contract}
{feedback}
