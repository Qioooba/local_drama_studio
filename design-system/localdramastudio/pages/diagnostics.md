# Diagnostics page override

> Applies to `/system/diagnostics`. The global `MASTER.md` remains authoritative for tokens, typography, controls, and accessibility.

## Product task

This page answers three operator questions and nothing else:

1. **Can this machine produce work now?**
2. **What requires action, and what is the next safe step?**
3. **What happened, or where is the affected entity?**

It is not a live telemetry wall, a second Settings area, a raw log viewer, or a duplicate project package/restore surface.

## Information architecture

Use three outcome-labelled tabs:

- **Environment status**: the default. One readiness summary, last-check time, one explicit non-destructive check action, actionable exceptions, then collapsed passed evidence.
- **Operation records**: redacted append-only audit history. Common filters stay visible; actor, subject and date filters are progressive disclosure. Hash proof is evidence, not the primary action.
- **Cross-project find**: semantic entity lookup with optional project scope. It does not expose UUIDs in ordinary results.

## Status semantics

- Red means a current check failed or a required production capability is unavailable.
- Amber means incomplete evidence, stale observations, or a condition that deserves attention but does not prove production is unavailable.
- Green means verified by the latest explicit run.
- Never derive `0` from a missing measurement. Render missing values as unknown.
- Runtime facts win over inventory metadata. Inventory is a bounded fallback and its source must remain visible in technical details.

## Disclosure rules

- Show only failed, blocked, and warning checks as open cards.
- Group passed checks by operator-facing category behind one collapsed disclosure.
- Raw check codes, endpoints, hashes, paths, IDs and redacted JSON live in technical details.
- Historical results always display their timestamp and become visibly stale after 15 minutes.

## Responsive rules

- At 1024px and above, summary facts and exception cards may use two columns.
- Below 768px, all content is a single column and audit events use cards instead of a wide table.
- Primary controls remain at least 40px on desktop and 44px for coarse pointers.

