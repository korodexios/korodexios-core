# Contributing to korodexios-core

The chassis is a foundation many projects stand on, so a change lands once, in one place,
and only when it serves more than one project. This is how an idea travels from a project
into the core without being built twice.

## 1. A project finds a gap

A project never edits its installed core. Its agent (or its owner) writes a note instead:

```bash
kdx proposal new "Metrics should support histograms"   # or the MCP tool kdx_proposal_draft
kdx proposal list
```

The note lives in the project (`docs/core-proposals/NNN-*.md`) next to a
`WORKAROUND(core-proposal NNN)` in the code. Nothing has left the machine.

## 2. The owner decides to send it

```bash
kdx proposal export 001             # exactly what would be sent (project name left out)
kdx proposal send 001               # lists similar issues, asks, then creates it with gh
kdx proposal send 001 --repo me/korodexios-core   # to a fork instead
```

`send` needs the GitHub CLI (`gh auth login`); without it, it prints the issue-form link and
the file to paste. The issue address is written back into the note, so it is not sent twice.

## 3. One idea, one issue

Before opening an issue, search open **and closed** issues labelled `core-proposal`. If the
idea is there, add your use case as a comment - a second use case is what moves a proposal
to *accepted*. The maintainer closes duplicates in favour of the oldest issue.

Labels: `core-proposal` (new) -> `accepted` (it belongs in the core; open for a PR) or
`rejected` (with the reason; usually "belongs in a project module").

## 4. Implementing an accepted proposal

Say so on the issue first, so two people do not build the same thing. Then:

```bash
git clone https://github.com/korodexios/korodexios-core && cd korodexios-core
uv venv --python 3.13 && uv sync --extra dev
git switch -c feature/<short-name>
make check                          # must be green before and after
```

To try the change inside the project that needed it, install your checkout there
(`uv pip install -e ../korodexios-core`) and remove the `WORKAROUND` once it works.

The rules are the ones in [AGENTS.md](AGENTS.md): stdlib first (a new dependency needs a
paragraph of justification), every capability usable from the CLI, tests for every branch,
the coverage floor never lowered, user-facing text through `tr()`, CHANGELOG updated. Open
the pull request with "Implements #NNN"; CI runs the full matrix.

## 5. Bugs

A bug report needs the command, the exact output, `kdx version`, and the OS. A fix comes
with a test that failed before it.
