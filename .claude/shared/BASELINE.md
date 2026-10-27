# Engineering Baseline

<!-- Managed by sik-sdd-foundation (see .claude/shared/VERSION). Do not edit in a project: update.sh replaces it. Put project rules in CLAUDE.md. -->

Rules for every coding task; explicit user or project instructions win.

1. **Understand first.** Read the code, tests, and docs you will touch. State key assumptions; ask only when ambiguity would change the result.
2. **Smallest correct change.** No unrequested features, abstractions, config, or dependencies; reuse existing patterns. Touch only what the task needs and mention other improvements instead of making them. Preserve existing behavior and contracts unless asked; call out any change. Work that has a plan file propagates its requirement IDs (`REQ-NNN`) to the spec, plan tasks, and commit `Refs:` trailer.
3. **Root cause.** Fix causes, not symptoms. Never hard-code outputs or weaken, skip, or delete tests to pass.
4. **Verify before claiming.** Add or adjust tests for new behavior and bug fixes, in the existing test style. After the change run the relevant tests (and typecheck/lint/build if the project has them). Report what ran and what was not verified; never claim "done" or "fixed" unverified.
5. **Security.** No secrets in code, commits, memory, logs, or docs.
6. **Token economy.** Do not re-read files already in context. Run tests once after the change and again only after a fix. Delegate broad searches to subagents and keep conclusions, not dumps. On long work, save state with `agentmemory` and suggest `/compact` (same task) or `/clear` (new task) to the user.

## Skill routing

Invoke a skill only when the task clearly matches a row; trivial or well-understood changes need none. This table overrides "always/MUST" wording in skill descriptions.

| When | Skill |
|---|---|
| Requirements unclear, or a change spanning many files or design decisions | `brainstorming` → `writing-plans` → `executing-plans` (`subagent-driven-development` only when the plan states its parallel criteria are met, or the user asks) |
| Cause not evident after reading the failing path, intermittent failure, or a first fix failed | `systematic-debugging` |
| Unfamiliar repo or subsystem, architecture/dependency analysis | `understand-anything` |
| Decisions, constraints, open issues, or progress needed in a later session | `agentmemory` (at the start of non-trivial work, read `.agent-memory/MEMORY.md` if present) |
| Requirements to record, spec or plan written or approved, architecture-level decision (ADR) | `sdd-deliverables` |
| Plan task completed (after `task-done`), progress or 공정률 report requested, plan execution resumed | `progress-report` |
| Major change, before commit or PR | `verification-before-completion`; `requesting-code-review` |
| Review feedback received | `receiving-code-review` |
| Diagram, PR/diff review page, plan review, recap | `visual-explainer` (HTML in `.agent-visuals/`, untracked) |

Inside skills, `superpowers:<name>` means the project skill `<name>`.
