# Phase 3 / K17 evidence — candidate reuse and adoption outcome

Date: 2026-09-13 (Asia/Shanghai)

Scope: isolated offline tests only. No real GPU/model execution and no production data/service mutation.

## Reproduced risks

- candidate lookup preferred creation time, so a newer candidate with a failed current-policy machine check could hide an older qualified candidate and provoke an unnecessary reroll.
- technical QC success was allowed to remain the overall production outcome even when automatic adoption returned `BLOCKED`.
- generation Variants and shot-owned verified videos were queried through separate fallback paths, which made selection and QC priority dependent on storage lineage.

## Implemented contract

- generation-variant and shot-owned verified video candidates are evaluated as one eligible set.
- current, non-stale manual authority wins first: approved candidate, then selected candidate.
- otherwise a candidate with `PASS` from the effective checker/policy wins over unchecked and failed candidates, regardless of creation time within those classes.
- FORMAL videos require `g6_formal_video_qc_v1`; other stages require `g4_media_qc_v1`. A result from another policy version is not reused as current proof.
- technical QC status and business adoption status remain separate. `PASS` plus adoption `BLOCKED` preserves the technical PASS while changing production status to `BLOCKED` and returning attention.
- existing bounded reroll policy remains responsible for generating a new take only after eligible candidates fail; deterministic configuration failures remain non-rerollable.
- existing machine-check uniqueness continues to make repeated same-media/same-checker/same-policy QC idempotent.

## Acceptance evidence

- an older current-policy PASS candidate is chosen ahead of a newer failed candidate, so no third candidate is created.
- a QC PASS whose adoption is blocked reports `production_status=BLOCKED` and requires attention without rewriting the technical result as FAIL.
- existing tests retain approved/selected candidate authority, bounded reroll behavior, current policy version matching, formal-video QC, and generation-variant adoption behavior.

## Verification

- 81 unique tests across episode actions/runs, reroll policy, G4/G6 QC, and generation variants: PASS.
- changed Python files: Ruff PASS.
- `git diff --check`: PASS (line-ending advisory warnings only).

## Boundary

K18 owns extraction of this eligibility decision into the shared production read model so Worker actions, workspace completion, and run views consume one definition. K17 does not add aesthetic scoring, turn technical PASS into approval, or run real media generation.
