---
name: understand-anything
description: Use when working in a repository or subsystem you have not yet studied in this session, when asked how something works or how parts fit together, for architecture or dependency analysis, for tracing complex control/data flow, or before modifying code whose existing behavior must be understood first. Builds an evidence-based map with file:line references. Skip for small, well-localized edits.
---

# understand-anything

Read-only investigation. Do not modify code while running this skill.
Scale depth to the question: one flow needs a trace, not a whole-repo survey.

## 1. Frame the question

State what must be understood and why (e.g. "how does checkout compute tax, so we can
add a region"). This bounds the search.

## 2. Existing knowledge first (hints, not truth)

`CLAUDE.md`, `.agent-memory/MEMORY.md`, `README*`, `docs/` (architecture, ADRs),
CI config. Verify anything you rely on against the code.

## 3. Orient (whole repository or subsystem)

- Size and shape: `git ls-files | wc -l`, top-level directories, languages.
- Manifests and lockfiles (package.json, pyproject.toml, go.mod, Cargo.toml, pom.xml, …):
  dependencies, scripts, build/test/lint commands.
- Entry points: main/cmd, server bootstrap, CLI, routes/handlers, jobs, exported API.
- Configuration and environment: config files, env var names (never print values).
- Recent activity: `git log --oneline -20 -- <area>`.
- For broad sweeps, dispatch read-only Explore subagents in parallel, one per area,
  and ask each for conclusions with `file:line` references.

## 4. Trace the relevant flow

From the entry point to the code in question: follow calls/imports; note data shapes,
side effects (DB, network, filesystem, queues), error paths, concurrency, and feature
flags. Find the tests covering it and how to run them.

## 5. Dependencies

Internal: which modules import the target, and what it imports (grep import/require/use).
External: libraries involved and their pinned versions.

## 6. Report

Concise, with `file:line` references:

- **Components** — responsibility of each relevant module.
- **Flow** — numbered steps of the traced path.
- **Dependencies** — internal and external.
- **Invariants / assumptions** — what the code relies on.
- **Risks & unknowns** — mark anything inferred but not verified as *unverified*.
- **Where to change** — the smallest set of places the task touches, and tests to run.

Only state what you read in the code; label inferences.

## 7. Visual map (when it helps)

When the user asks for a picture or map, or the result spans many components (roughly 6+),
also render the report with the `visual-explainer` skill: a Mermaid dependency/flow diagram
with every arrow labeled, plus component cards with `file:line` references. Keep the
chat reply to a short summary.

## 8. Persist (optional)

If a finding will matter in later sessions (a constraint, an architectural fact that
is costly to rediscover), record it via the `agentmemory` skill. Write docs only when asked.

## Optional: upstream knowledge-graph plugin

On a machine where the upstream Understand-Anything Claude Code plugin is installed
(`/understand-anything:understand`, https://github.com/Egonex-AI/Understand-Anything),
it can build a `.ua/knowledge-graph.json` and dashboard. Use it only if the user asks;
this skill does not depend on it.
