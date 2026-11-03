# Project conventions

The rules every Korodexios project follows, written so that a person who does not
program and an AI coding agent can both apply them. Short on purpose: if a rule is not
here, ask before inventing one. English is the canonical text; project
translations follow the chassis locale (`kdx locale`).

**How to use this file.** Keep it in the project (`docs/CONVENTIONS.md`). Tell every AI
agent at the start of a session: *"Read AGENTS.md and docs/CONVENTIONS.md first."*

## 1. The idea in five lines

1. Every project is built on the Korodexios core (`kdx`): a small, stable chassis.
2. A project is **many small modules** (plugins), never one large program.
3. Everything must work from the terminal first; a web page is only an extra layer.
4. Nothing is "done" until the automatic checks pass (`make check`).
5. Anything risky or hard to undo needs a human to say yes first.

## 2. Structure: small pieces

AI agents (and people) make fewer mistakes in small pieces of code, so size is a rule:

| Thing | Limit | If it grows |
| --- | --- | --- |
| a function | about 40 lines | split it |
| a file | about 400 lines | split into two modules |
| a module | one job, described in one sentence | make a second module |
| a function's arguments | at most 5 | group them in a small data class |

Module rules:

- A module is a folder under `src/<project>/modules/<name>/` with `module.py` and a
  `kdx_module.toml` manifest. The core discovers it by itself; nobody edits a central list.
- Modules **never import each other**. They talk through the event bus, the registry, or
  the control plane. Then any module can be removed, replaced, or tested alone.
- A module declares its settings (`config_schema`), reads them only through the core,
  and releases everything it started in `stop()`.
- A module writes diagnostics to stderr and never prints to stdout; only the CLI layer
  prints for people.
- No stubs: no `TODO` bodies, no empty `pass`, no fake return values. If a branch exists,
  it runs and has a test.

## 3. Starting a project from zero

```bash
kdx init my-project        # asks a few questions, creates the whole skeleton
cd my-project
uv sync --extra dev        # or: pip install -e ".[dev]"
make check                 # must be green before the first change
kdx modules create billing  # one new module (a folder with module.py and a manifest)
kdx doctor                 # the project explains its own state
```

The skeleton contains: `AGENTS.md` (the contract for agents), `docs/` (architecture and
these conventions), `kdx.yml` (configuration), `tests/`, `.github/workflows/ci.yml`
(the automatic checks on every change), and a `Makefile`.

## 4. How we work: spec, test, code, check

These short names are all the same idea: **decide before you build, prove after you build.**

| Name | Meaning in plain words | How we use it |
| --- | --- | --- |
| **SDD** (spec-driven) | Write what you want before anyone writes code. | A feature starts as a half-page spec in `docs/specs/NNN-name.md` (start from `docs/specs/TEMPLATE.md`): the goal, what it will *not* do, how it behaves, and how we will know it works (acceptance criteria). |
| **TDD** (test-driven) | Write the failing test first, then the code that makes it pass. | Red (test fails) → green (smallest code passes) → clean up. An agent is told: "write the test first, show it failing, then implement." |
| **BDD** (behaviour-driven) | Describe behaviour as "given / when / then". | Acceptance criteria and test names read like sentences: `test_stopping_a_job_that_already_ended_says_so`. |
| **ADR** (decision record) | A note that says why we chose X. | One short file per big decision in `docs/decisions/NNN-title.md`: context, decision, consequences. Never edit an old one; write a new one that replaces it. |
| **CI** | The computer runs all checks on every change. | `.github/workflows/ci.yml` runs the same checks as `make check` (lint, strict types, tests) on Linux and Windows for every push and pull request. |
| **CD** | The computer ships a release when the checks pass. | A release is a git tag (`v1.2.0`) on a green `main`; the workflow builds the package from the tag. Nothing is deployed from a laptop. |

You do not need domain-driven design, microservices, or any other big method. Modules,
specs, tests, and automatic checks are enough.

**The loop for every change** (one change = one small goal = one commit or pull request):

1. Say the goal in one sentence. If it needs more than a few modules, split it.
2. Write or update the spec / acceptance criteria. Write spec rules as MUST / MUST NOT /
   SHOULD, so people and agents can tell a hard rule from advice. A large feature is
   split into numbered phases; one phase is implemented at a time.
3. Write the failing test.
4. Write the code until the test passes.
5. Run `make check` (lint, strict types, tests, coverage floor). Fix, repeat.
6. Update docs, `CHANGELOG.md`, and the translations if text for people changed.
7. Commit with a clear message; open a pull request; a human reads the diff and merges.

Found something outside the task? Record it in the project's backlog (issue tracker or
the notes file the project uses) and continue the task. Do not fix it in this change.

## 5. Definition of done

A change is done only when **all** of these are true:

- [ ] `make check` passes (ruff, mypy `--strict`, pytest, coverage not lower).
- [ ] There is a test that fails without the change.
- [ ] It works from the CLI (and `--json` where a script would use it).
- [ ] Help text and messages are clear, say what to do next, and are translatable.
- [ ] No secret, token, password, or personal data is in the code, logs, or git.
- [ ] Docs and `CHANGELOG.md` are updated; the version is bumped only when releasing.

## 6. Security rules

- **Secrets** live in environment variables or `.env` (never committed; `.env.example`
  lists the names only). Logs, errors, and `--json` output never contain secrets.
- **Default closed**: listen on `127.0.0.1`, require a token, give each person the lowest
  role that works (viewer < operator < admin). Turning protection off is explicit.
- **Check all input** at the edge (CLI arguments, files, network). Never build shell
  commands or file paths from unchecked text; never use `eval`/`exec`.
- **Few dependencies**: standard library first; a new dependency needs a written reason,
  a pinned version, and a check with `pip-audit`.
- **Least surprise on disk**: write only inside the project (`var/`, `data/`); never
  delete or overwrite what you did not create without asking.
- **Updates**: keep the lock file committed; update dependencies in their own small change.
- **Every feature** gets one sentence: "what could go wrong, and who could abuse it?" in
  its spec.

## 7. Ask first (a human must say yes)

AI agents must stop and ask before: `git push`, tagging a release, merging to `main`,
deleting files or data, changing credentials or permissions, installing a service on the
machine, adding a dependency, spending money, or sending data to an outside service.
Approval for one thing is not approval for the next.

## 8. Working with AI agents

- Give **one small task at a time**. A small task has a clear end ("add `--json` to
  `billing list`"), not a direction ("improve billing").
- Always point the agent at `AGENTS.md` and this file; keep both short and current.
- Ask for the plan first when the change touches more than one module.
- Ask the agent to **show the failing test, then the passing run** — not "it should work".
- Read the diff. If you cannot follow it, ask for a smaller one or a plain-language
  explanation. You may always say "explain it simply" or ask for answers in your own
  language.
- Start a fresh session for a new task; long sessions drift. Put what must survive
  (decisions, rules) in files in the repository, not in the chat.
- Agents commit to a feature branch only. A human decides about push, tag, and merge.

## 9. Words and look (CLI first)

- Every command has `--help` that explains in plain words, with an example.
- Errors say what happened and what to do next, in one or two lines; no stack traces
  unless `-v`.
- Output for people goes to stdout, diagnostics to stderr, machine output with `--json`.
- Status tags: `[✓]` done, `[?]` question, `[!]` problem, `[i]` information. Colour helps
  but is never the only signal (it must read well without colour and when piped).
- Exit codes: 0 ok, 1 failure, 2 usage error, 3 config error, 130 cancelled.
- Commands and flags stay English everywhere; the text people read is translated
  (`kdx locale`). Add a language with `docs/TRANSLATING.md`.
- Anything that asks questions has a default in `[brackets]`, accepts Enter, prefers
  numbered choices over free typing, and has `--yes` for scripts. Never hang waiting
  when there is no terminal.

## 10. Git habits

- One feature = one branch (`feature/short-name`); `main` always passes `make check`.
- Commit messages start with a verb: `add:`, `fix:`, `change:`, `docs:`, `release:`.
- Small commits that each pass the checks. Never commit `var/`, `.env`, or build output.
- Versions follow semantic versioning: `MAJOR.MINOR.PATCH` (breaking, feature, fix).

## 11. Day-0 checklist for a new project

1. `kdx init my-project` and `make check` is green.
2. Write `docs/specs/001-<first-feature>.md` from `docs/specs/TEMPLATE.md` (goal,
   not-goals, behaviour, acceptance).
3. Create a repository, protect `main` (no direct push, CI must pass).
4. Add the CI workflow secrets only if really needed; none are needed to run `make check`.
5. Decide the languages you want (`kdx locale add xx`).
6. Start the first module with the test first.
