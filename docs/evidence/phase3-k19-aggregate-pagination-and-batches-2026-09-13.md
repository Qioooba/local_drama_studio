# Phase 3 / K19 evidence — aggregate, pagination, and director batches

Date: 2026-09-13 (Asia/Shanghai)

Scope: isolated offline tests only. No production data/service mutation.

## Reproduced risks

- the episode workspace calculated every stage total from the first bounded detail request (`limit=100`), so shot 137 could not affect the displayed stage state.
- attention cards were drawn from that same first page, so a failure outside it was not locatable.
- Director batch membership was intersected with the ±25-shot navigator window, silently shrinking a confirmed batch when a member was outside the current window.

## Implemented contract

- the canonical production overview now publishes `stage_summary`, aggregated from the complete episode projection before pagination.
- every stage summary preserves total, completed, running, attention, stale, and human-confirmation counts.
- the workspace consumes the authoritative summary for stage progress while keeping the ordinary shot detail request bounded at 100.
- attention detail is a separate filtered bounded query, so a lone failure beyond the first detail page remains discoverable without loading the entire episode.
- Director batch IDs come from the confirmed URL/session batch. The navigator remains a local window and has no authority to remove members.
- navigating to an off-window batch member loads that exact shot through the existing server route; invalid/cross-episode IDs still fail through server validation instead of being mislabeled as merely unloaded.
- the generated client template and OpenAPI artifact include the additive overview field.

## Acceptance evidence

- a synthetic 137-shot projection with shots 1–136 ready and shot 137 failed reports VIDEO total 137, completed 136, attention 1.
- the workspace test supplies only 100 detail rows but renders the overview's 137-shot stage result.
- a Director batch containing an off-window second member remains `1 / 2`, navigates to it, and becomes `2 / 2`.
- ordinary detail pagination remains capped at 100; no `limit=10000` workaround was introduced.

## Verification

- 11 backend production-read tests: PASS.
- 30 frontend workspace/director tests: PASS.
- Python Ruff and `git diff --check`: PASS (line-ending advisory warnings only).
- TypeScript compilation reaches only the pre-existing unrelated `LocalLLMConfigurationPanel.tsx:181` error; no K19 TypeScript error is reported.

## Boundary

The attention detail query deliberately remains bounded; the overview is the full-count authority. K19 does not make frontend caches authoritative and does not expand batch write limits.
