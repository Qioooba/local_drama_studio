# Phase 3 / K20 evidence — contact sheets use V2 working slots

Date: 2026-09-13 (Asia/Shanghai)

Scope: isolated local export tests only. No production data/service mutation.

## Reproduced risk

The contact-sheet query required `media_assets.selected_version_id` plus a legacy `selections` row owned directly by a SHOT. A V2 adoption can instead point `shot_working_media_slots` at a MediaVersion (including generation-variant lineage) without creating that legacy mirror, so the current working result was omitted.

## Implemented contract

- current `KEYFRAME` and `VIDEO` working slots are the primary export authority.
- the query follows the slot's exact MediaVersion and media asset regardless of whether the immutable asset is shot- or generation-variant-owned.
- legacy SHOT selections remain a compatibility fallback only when that shot has no current working slot of the same kind.
- one slot contributes exactly its selected version; a newer asset/version elsewhere does not replace it.
- archived shots and non-verified media are excluded by the selection query.
- export identity, controlled-path enforcement, source SHA/size checks, thumbnail generation, tamper detection, and idempotent reuse remain unchanged.

## Acceptance evidence

- a V2-adopted PROXY with zero legacy selection rows exports successfully and appears in the manifest.
- when legacy video A remains selected but the V2 VIDEO slot points to B, only B is returned; A is not double-counted.
- the legacy-only fixture still exports, proving compatibility fallback.
- repeated exact export reuses the verified directory; changed source or tampered export is rejected.

## Verification

- 6 contact-sheet service/API/integrity tests: PASS.
- changed Python files: Ruff PASS.
- `git diff --check`: PASS (line-ending advisory warnings only).

## Boundary

This makes the export represent current choices; it does not approve those choices or create a second media mirror.
