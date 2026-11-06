# NNN - Title

Status: proposed | accepted | rejected | done     Date: YYYY-MM-DD     Raised in: <project>
Issue: (not sent)

A project does not edit the core. When the core is wrong or missing something, the
agent writes this note and carries on with a workaround that names it:
`WORKAROUND(core-proposal NNN)`. Nothing leaves this machine until the owner runs
`kdx proposal send NNN`, which shows the exact text first.

## Problem
What fails or is missing, in one or two sentences.

## Where it showed up
The command, module or file, and the exact output or behaviour.

## Proposed change
What the core should do instead. Contract only, not implementation.

## Why it belongs in the core
Who else needs it: which kinds of projects, not only this one. A need only this project has
belongs in a project module.

## Impact
New or changed API, config keys, exit codes. Is it backward compatible?

## Workaround until then
What the project does now, and where (`WORKAROUND(core-proposal NNN)` in the code).

## Implementation offered
none | a branch or fork with the change and its tests (link it)
