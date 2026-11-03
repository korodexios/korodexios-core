# NNN-short-name - Title

Status: draft | approved | built     Date: YYYY-MM-DD     Asked for by: <who>

## Goal
One sentence: what changes, for whom.

## Non-goals (explicit)
What this change will NOT do. Anything not here is out of scope; the agent records
findings instead of adding them.

## Constraints
- MUST ...
- MUST NOT ...
- SHOULD ...
(exact versions, forbidden patterns; one line each)

## Behaviour
- given <state>, when <action>, then <result>
(one line per behaviour; each line becomes a test name)

## Data and interfaces
Schemas, types, event names, config keys - contracts only, never implementation.

## Phases (only for larger features)
1. ...   2. ...   - one phase at a time; the next starts only when the previous
passes `make check`.

## Edge cases and failure paths
empty input, max+1, offline, permission denied, cancelled mid-run, re-run.

## What could go wrong / who could abuse it
One or two sentences.

## Acceptance
Exact commands and expected output. Done when these pass - not when the code
"looks fine".
