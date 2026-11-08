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

AI agents (and people) make fewer mistakes in small pieces of code. What actually hurts
is **more than one reason to change per file**, not the line count: a 1300-line file of
small functions around one job is safer to change than a 380-line file holding three
jobs. So these numbers are **triggers for a decision, not caps**:

| Thing | Trigger | When it fires |
| --- | --- | --- |
| a function | about 40 lines | split it, or say why it stays whole |
| a file | about 400 lines | ask how many jobs it holds; split by job, not by line |
| a module | one job, described in one sentence | make a second module |
| a function's arguments | more than 5 | group them in a small data class |

- **Splitting a module is a decision about jobs, never about lines.** A module boundary
  costs a manifest, a config namespace, a durable-state owner and `peer()` calls; it pays
  for itself only when a piece can be removed, replaced or tested alone. A long file with
  one job is split into files inside the module, not into a second module.
- **The debt is measured and may only shrink.** `tests/test_size_ratchet.py` (seeded by
  `kdx init`) names every file, function and signature over its trigger and fails when
  the list grows, or when a recorded number is left higher than reality. Clean something,
  lower the number; never raise it: the same ratchet the coverage floor uses.

Nothing else about size is a rule: no comment to ask permission, no stop to ask the
owner, no module split to please a counter.

Module rules:

- A module is a folder under `src/<project>/modules/<name>/` with `module.py` and a
  `kdx_module.toml` manifest. The core discovers it by itself; nobody edits a central list.
- Modules **never import each other**. They talk through the event bus, the registry, or
  the control plane. Then any module can be removed, replaced, or tested alone.
  `tests/test_rules.py` (seeded by `kdx init`) fails on a cross-module import and on a stub
  (a function that is only `pass`, `...` or `raise NotImplementedError`, or a `TODO` comment).
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
kdx brief                  # the interview: what must it do, what must it not do
uv sync --extra dev        # uv is the standard; pip only where uv cannot be installed
make check                 # must be green before the first change
kdx modules create billing  # one new module (a folder with module.py and a manifest)
kdx doctor                 # the project explains its own state
```

The skeleton contains: `AGENTS.md` (the contract for agents), `docs/` (architecture and
these conventions), `kdx.yml` (configuration), `tests/`, `.github/workflows/ci.yml`
(the automatic checks on every change), and a `Makefile`.

**Tools: uv and ruff.** Every project uses the same two tools, so people and agents never
have to guess:

- **uv** (Astral) manages Python, the virtual environment, and dependencies. Use
  `uv venv`, `uv sync --extra dev`, `uv add <package>`, `uv lock`, and `uv run <command>`
  (for example `uv run kdx doctor`, `uv run make check`). Do not call bare `pip` or
  `python -m venv`, and do not install into the system Python. Commit `uv.lock`. `pip`
  with a plain virtual environment is only a fallback for a machine where uv cannot be
  installed.
- **ruff** is the only linter and formatter: `ruff check` for lint and `ruff format` for
  layout, both configured in `pyproject.toml`. Do not add black, flake8, isort, or pylint.
  Run them as `uv run ruff check src tests` and `uv run ruff format src tests`; `make lint`
  and `make check` do the same. A rule is silenced only with a narrow `# noqa: CODE` and a
  reason, never by turning a rule off for the whole project without asking.

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

1. Say the goal in one sentence and check it against `docs/BRIEF.md`. If it needs more than
   a few modules, split it.
2. Write or update the spec / acceptance criteria. Write spec rules as MUST / MUST NOT /
   SHOULD, so people and agents can tell a hard rule from advice. A large feature is
   split into numbered phases; one phase is implemented at a time.
3. Write the failing test.
4. Write the code until the test passes.
5. Run `make check` (lint, strict types, tests, coverage floor). Fix, repeat.
6. Update docs, `CHANGELOG.md`, and the translations if text for people changed.
7. Commit with a clear message; open a pull request; a human reads the diff and merges.

Found something outside the task? Record it in `docs/BACKLOG.md` (or the project's issue
tracker) and continue the task. Do not fix it in this change. Section 15 says what to do
with a bug that blocks the work.

## 5. Definition of done

A change is done only when **all** of these are true:

- [ ] `make check` passes (ruff, mypy `--strict`, pytest, coverage not lower), run through
      `uv run`, and the code is formatted with `ruff format`.
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
  a pinned version (added with `uv add`), and a check with `pip-audit`.
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
- If the goal, a behaviour or a limit is unclear, the agent asks; it never guesses.
  `docs/BRIEF.md` (`kdx brief`) holds the answers; `kdx brief --check` says if it is complete.
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

## 11. The core is never edited from a project

The core is shared by every project, so a project only uses it. When the core is wrong
or lacks something, write a proposal (`kdx proposal new "<title>"`, saved in
`docs/core-proposals/`) and keep going with a workaround marked
`WORKAROUND(core-proposal NNN)`, so it can be found and removed once the core is fixed.
`kdx proposal list` shows what is open.

## 12. Day-0 checklist for a new project

1. `kdx init my-project` and `make check` is green.
2. Run `kdx brief` and answer the questions: goal, users, core behaviour, non-goals, done-when.
3. Write `docs/specs/001-<first-feature>.md` from `docs/specs/TEMPLATE.md` (goal,
   not-goals, behaviour, acceptance).
4. Create a repository, protect `main` (no direct push, CI must pass).
5. Add the CI workflow secrets only if really needed; none are needed to run `make check`.
6. Decide the languages you want (`kdx locale add xx`).
7. Start the first module with the test first.

## 13. Do not break what works (safety nets)

Most damage in agent-written code is a working feature silently broken by an unrelated
change. These rules make that loud and early:

- **Never make a check green by weakening it.** Do not delete or skip a test, lower the
  coverage floor, add a blanket `# noqa` / `# type: ignore`, or loosen a type to get past a
  failure. Fix the code, or stop and ask.
- **Bug fix = regression test first.** Reproduce the bug as a failing test, then fix it.
  The test stays forever.
- **Before changing old code, pin its behaviour.** If it has no test, write one that records
  what it does today (a characterization test), then change it.
- **Contracts are tested.** The `--json` output of each command, the exit codes, and the
  API schema (`GET /api/v1/schema`) are public. A test compares them to a saved expected
  copy (a snapshot), so changing one on purpose is a visible diff and never an accident.
- **Tests are independent and fast.** No network, no real clock waits, no shared state
  between tests; a test passes alone and in any order.
- **One change, one commit, one revert.** Small commits that each pass `make check` mean a
  bad change is undone with a single `git revert`. Tag known-good states before a risky
  refactor.
- **Refactor and feature never share a commit.** Behaviour must not change in a refactor;
  the unchanged tests prove it.
- **A second pair of eyes.** Before merging, review the diff in a fresh session or with a
  different agent ("review this diff for bugs and security problems, change nothing").
  The author is the worst reviewer of its own change.

### Tools that guard quality

**Already in a seeded project** (`make check` and CI run them; nothing to install by hand):

| Need | Tool | What it does for you |
| --- | --- | --- |
| Style, bugs | **ruff** | One fast tool for lint and format. Turn on more rule sets over time (`S` security, `PTH` paths, `PT` pytest style). |
| Types | **mypy --strict** | Catches wrong calls before they run. |
| Behaviour | **pytest + coverage floor** | The floor (`fail_under` in `pyproject.toml`) only goes up. |
| Structure | **`tests/test_rules.py`, `tests/test_size_ratchet.py`** | Modules never import each other; no stubs; the size excess may only shrink. |
| Documents | **`kdx docs check`**, **`kdx agents sync --check`** | The conventions, the playbook and every agent's rule file are current. |
| Vulnerable dependencies | **pip-audit** (CI, `make audit`) | Fails when a locked dependency has a known hole. |
| Auto-run at commit | **pre-commit** (`make hooks`, optional) | Runs the cheap checks on commit and `make check` on push. |

**Worth adding when the project needs it** (a dependency decision, section 7: ask first):
**gitleaks** or GitHub secret scanning for committed secrets, **Dependabot** or Renovate
for small update pull requests, **CodeQL** for injection patterns, **Hypothesis** for
parsers and validators, **mutmut** now and then to see whether the tests really test.

When working with an AI agent, also: keep `AGENTS.md` short and current, give one small
task at a time, ask it to show the failing test and then the passing run, and run the
checks yourself (`make check`) instead of trusting "it works".

## 14. One core, many ways to control it (CLI, GUI, API)

The core is headless: it runs the work, and every front end only asks it. So the same
capability can be driven from a terminal, a web page, a script, another program, or an AI
agent, with identical results.

- **One implementation, many doors.** Logic lives in a module (a command or an
  `ipc_routes()` handler). The CLI command, the web console, and the API call all reach that
  one function. Never write the logic in a front end.
- **The API is the contract.** A running daemon answers over local HTTP at `/api/v1`:
  `GET /api/v1/schema` describes every route, `POST /api/v1/rpc` runs a registered module
  command, and modules add their own routes. Anything a GUI button does, `curl` or a
  script can do through the same call.
- **Machine-readable by default.** Every command that reports something has `--json`; the
  result is the same data the API returns. Humans get tables, programs get JSON.
- **Typed, validated input.** Each command and route declares its arguments, and the
  core checks them at the edge. A GUI form, a CLI flag, and an API field are validated by
  the same rule.
- **Same permissions everywhere.** Roles (viewer, operator, admin) and the token apply to
  every door equally; a GUI is never a way around them. The caller is recorded in the
  audit log.
- **Long work is a job.** A task that takes more than a moment runs as a background job or
  in the durable queue (`kdx queue`) and returns an id; any front end can then ask for its
  state, cancel it, or read the result. Nothing waits on a screen.
- **Events, not polling.** Progress and changes are published on the event bus so a GUI,
  a log, and another module can all react without knowing each other.
- **Terminal first, GUI as an extra.** A feature is not done until it works from `kdx`; the
  GUI may only be a nicer way to reach the same thing.
- **Version the contract.** Changing an existing route or JSON field is a breaking change:
  add a new field or a new version instead, and note it in `CHANGELOG.md`.

## 15. Found a bug? Park it, keep the direction

A long session loses the thread: an agent that dives into a side bug forgets the goal,
loops on the same fix, and leaves the project half-changed. So the project keeps its own
memory in files, and a bug never takes over the session.

**The two files** (create them when missing; both are short and always current):

- `docs/STATUS.md` - the goal in one sentence (from `docs/BRIEF.md`), the current task,
  what is done, what is next, the exact command that shows the baseline, and any open
  question. It is the first thing read in a session and the last thing updated.
- `docs/BACKLOG.md` - one line per parked item: date, what was seen, where
  (file and command), how to reproduce it, how serious it is.

**The rule: park first, fix later.**

1. **Does the bug stop the current task?** If not, write one line in `docs/BACKLOG.md` and
   continue the task. Do not fix it now, do not "just quickly" touch it.
2. **If it does block the task**, say so in `docs/STATUS.md` ("blocked by: ..."), then fix
   only that bug, in its own small change: a failing regression test, the smallest fix,
   `make check` green, its own commit. No other cleanup rides along.
3. **Limit the effort.** After three failed attempts or about thirty minutes, stop. Write
   down what was tried and what was learned in `docs/BACKLOG.md`, and ask the owner. Never
   try the same fix a fourth time.
4. **Reset the context after a big detour.** When the fix needed long debugging, finish it,
   commit, update `docs/STATUS.md`, and start a fresh session. The new session reads
   `AGENTS.md`, `docs/BRIEF.md`, `docs/STATUS.md` and the current spec, so it resumes the
   original task with a clean head and the full project picture, not the debugging noise.
5. **Back on the task.** Before continuing, run `make check` and re-read the spec's
   acceptance criteria. The detour is over only when the original goal is in front of you
   again.

Bugs found in the core are not fixed from the project: see section 11 (`kdx proposal new`).

Review `docs/BACKLOG.md` between tasks, with the owner, and turn the important items into
their own small tasks.

## 16. Building a larger GUI (recommendation)

Section 14 says a GUI is only another door. When one grows past the built-in console
(`kdx ui`), keep these:

- **Extend before you add.** Read `modules/web_console` first (plain HTML, CSS and
  JavaScript, no build step); a few more panels belong there, not in a second front end.
- **Python stays the runtime.** The GUI is built into static files the core serves
  (`ipc_static_dirs` / `IpcStaticMount`); bun or node exist only on the developer's machine.
  If the project wants a toolchain: bun, Vite + TypeScript, Biome, with the framework chosen
  per project. Same bar as Python: strict types, lint clean, a build that succeeds.
- **Types come from the contract.** Build client types from `GET /api/v1/schema`; when a
  route changes, the GUI build fails, not the user's screen.
- **No logic in the screen.** Validation, permissions, long jobs and state live in the
  module, so CLI and API get them too (section 14).
- **Safe and translated.** Values from the daemon are text, never HTML (no `innerHTML`); the
  token stays out of URLs and logs; user-visible strings use the `korodexios-web` catalogue.
- **Terminal parity check.** Before calling a GUI feature done, run the same action as
  `kdx ... --json` and confirm the result matches the screen.
