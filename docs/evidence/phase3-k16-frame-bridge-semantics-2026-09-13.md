# Phase 3 / K16 evidence — frame-bridge semantics and predecessor dependency

Date: 2026-09-13 (Asia/Shanghai)

Scope: isolated offline tests only. No real GPU/model execution and no production data/service mutation.

## Reproduced risks

- episode automation treated the predecessor's tail as the current shot's `END_FRAME`; this is the opposite side of a last-to-first bridge and could produce the invalid B/A/R input ordering described by K16.
- automatic chaining considered two missing scene ids equal and therefore eligible for continuity.
- adopting any video silently created a transition and wrote a synthetic LAST_FRAME anchor whose extracted media id was the video itself.
- the chaining idempotency key included a newly generated anchor id, so replay could create another equivalent boundary.
- `inherit` accepted a non-LAST source anchor and did not protect a different locked current-start frame.

## Implemented contract

- a predecessor tail is only a candidate for the successor's start/continuity input; episode automation never inserts it as `END_FRAME`.
- scene continuity requires two non-empty, equal stable `scene_id` values. Matching or empty environment strings have no authority.
- video adoption no longer creates a bridge. Explicit chaining requires an existing, fresh transition with an inheriting constraint type plus a verified tail IMAGE owned by the predecessor lineage.
- the event key is stable over transition/from/to/tail MediaVersion/SHA. Replaying the same event returns the stored command result and creates no second inherited anchor.
- `FrameBridgeCommandService.inherit` requires a fresh `LAST_FRAME` source. A HARD/LOCKED bridge with a different current-start anchor fails atomically before inserting an inherited anchor or moving the pointer.
- episode video dispatch evaluates HARD bridge dependencies before creating a Variant/Job:
  - missing predecessor working video or incomplete/stale bridge -> `HARD_FRAME_BRIDGE_WAITING`;
  - frozen bridge/current approved keyframe mismatch -> `HARD_FRAME_BRIDGE_CONFLICT`;
  - cuts, advisory continuity, unknown scenes, and no explicit transition do not inherit.
- a satisfied HARD bridge records transition id, source/current anchor ids, predecessor working MediaVersion, source SHA, frame index, and source time in dispatch evidence.

## Acceptance evidence

- same explicit event replay creates one inherited anchor and reports idempotent replay.
- unknown scenes and scene cuts do not inspect or inherit tail media.
- video adoption alone creates no transition.
- a locked current frame B conflicting with predecessor tail A returns `FRAME_BRIDGE_LOCKED_CURRENT_CONFLICT`; boundary revision and B pointer remain unchanged and no inherited anchor exists.
- real tail extraction/source-frame tests retain source video version, frame position, extraction method, and SHA evidence.
- approval-impact tests continue to stale only anchors, HARD constraints, and Variants that actually reference the replaced working media; independent shots are not selected by positional adjacency.
- an explicit HARD bridge with unfinished predecessor returns a waiting fact before shot submission.

## Verification

- 81 unique tests across frame chaining/commands, generation dependency impact, episode dispatch/run behavior, continuity context, and review annotations: PASS.
- changed Python files: Ruff PASS.
- `git diff --check`: PASS (line-ending advisory warnings only).

## Boundary

K22/K23 still own broad automatic continuation and external-submit recovery. K16 deliberately pauses unresolved HARD continuity instead of pretending GPU serialization supplied a tail frame. No Motion Context or whole-drama continuous-chain feature was enabled.
