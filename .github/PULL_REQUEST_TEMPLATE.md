## What and why

<!-- One or two sentences. Link the accepted proposal: "Implements #123". -->

## Checklist

- [ ] It implements an **accepted** `core-proposal` issue (or fixes a bug with a test that failed before).
- [ ] `make check` is green locally (ruff, `mypy --strict`, pytest, coverage floor).
- [ ] No new runtime dependency (or the issue says why one is needed).
- [ ] Every new capability works from the CLI (`kdx ...`, `--json` where scripts use it).
- [ ] User-facing text runs through `tr()`; `kdx locale extract` was run and `sk` is filled in.
- [ ] `CHANGELOG.md` (`[Unreleased]`) and the docs that describe the behaviour are updated.
- [ ] Nothing from `var/`, `.env`, `kdx.local.yml`, tokens or user data is committed.
