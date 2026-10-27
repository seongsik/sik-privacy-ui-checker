---
name: agentmemory
description: Use when starting non-trivial work or when the user says "resume", "where were we", "continue" (restore context), and whenever a decision with its reason, an architectural decision, an important constraint, an unresolved issue, or progress/next steps must survive into the next session (record it). Project-scoped memory in .agent-memory/MEMORY.md, committed with the repo. Not for facts readable from code or git history; never for secrets.
---

# agentmemory (project-scoped, file-based)

Memory lives only in `<repo-root>/.agent-memory/MEMORY.md` (`git rev-parse --show-toplevel`).
It is plain Markdown, reviewed in diffs, and committed like code, so it survives
ephemeral environments (e.g. Claude Code Remote) and never mixes between projects.
(It is deliberately outside `.claude/`, which Claude Code treats as protected for edits.)

## Isolation rules

- Read and write only this repository's `.agent-memory/`. Never read or write another
  repository's memory, `~/.claude`, or the central standard repository.
- If the user wants something shared across projects, it belongs in the central
  standard (BASELINE/skills), not in memory.

## Restore (read)

1. At the start of non-trivial work (or on "resume"), read `.agent-memory/MEMORY.md` if it exists.
2. Lead with **Current state** and **Open issues** relevant to the task.
3. Treat entries as hints: verify against the code before relying on them; fix or
   delete entries that turn out stale.

## Record (write)

Write at the moment something settles, not in an end-of-session dump:

- **Decisions** — a choice was made; record the reason and rejected alternative if useful.
  Architecture-, interface- or dependency-level decisions go to `docs/decisions/ADR-NNN-*.md`
  (sdd-deliverables skill); keep only a one-line summary with the ADR link here.
- **Constraints** — a non-obvious limit discovered (API quirk, env limit, compat rule).
- **Open issues** — something known broken/undecided that was left for later.
- **Current state** — when work spans sessions: what is done, what is next, any blocker.

Do not write: anything derivable from code, tests, or `git log`; step-by-step
narration; speculation; personal data.

## Never store secrets

No passwords, API keys, tokens, private keys, connection strings, or their values —
not even partially. Refer to them by name/location only (e.g. "uses env `STRIPE_KEY`").
If a secret is found in memory, remove it and tell the user it may need rotation.

## Format and hygiene

Create the file from this template if missing:

```markdown
# Project Memory
<!-- agentmemory skill. Project-scoped, committed with the repo. No secrets. -->

## Current state
- (YYYY-MM-DD) <in progress / next step / blocker>

## Decisions
- (YYYY-MM-DD) <decision> — <reason>. [<files>]

## Constraints
- <constraint> — <why it matters>

## Open issues
- (YYYY-MM-DD) <issue> — <what is known>
```

- One line per entry when possible; update entries in place instead of appending duplicates.
- Keep the file under ~200 lines: delete resolved issues and finished state; fold
  resolved issues that produced a decision into **Decisions**.
- Include memory changes in the same commit as the work they describe. Uncommitted
  memory is lost when a remote session's container is reclaimed.
- After writing, tell the user in one line what was recorded.
