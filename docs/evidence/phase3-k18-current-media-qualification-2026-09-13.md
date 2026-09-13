# Phase 3 / K18 evidence — current media qualification read model

Date: 2026-09-13 (Asia/Shanghai)

Scope: isolated offline tests only. No real GPU/model execution and no production data/service mutation.

## Reproduced risks

- the episode-run view counted any historical verified video and any historical PASS, while the production workspace used the current working slot and dynamic lineage.
- candidate counts included damaged and stale variants.
- the latest machine check of any policy could qualify a selected video even when the effective G4/G6 policy had changed.
- a later failed generation Job hid an unaffected valid working result.
- any historical episode render counted complete even when the current timeline, selected audio, subtitle, source hash, or compose fingerprint differed.

## Implemented contract

- `queries/media_eligibility.py` is the shared storage-level resolver used by both episode automation and the canonical production repository.
- eligible candidates must belong to the shot, have the expected media kind/stage, be integrity `VERIFIED`, and, for generation variants, be non-stale.
- candidate counts, worker reuse, working-slot qualification, and current-policy QC now consume that resolver.
- the production repository continues to add dynamic prompt/profile/reference/asset-state freshness on top of storage eligibility.
- a current eligible working slot remains product-ready when a later Job fails or the run is cancelled; the Job/run failure remains separately visible.
- a selected video only carries QC proof from its effective current policy (`g4_media_qc_v1` or `g6_formal_video_qc_v1`). Another candidate's or obsolete policy's PASS cannot qualify it.
- the episode-run stage counts are derived from the canonical shot read model rather than duplicate media/QC SQL.
- render completion calls the existing compose preflight for the latest timeline and requires an intact exact-input `existing_render`; historical render rows cannot satisfy changed timeline/audio/subtitle/source fingerprints.

## Acceptance evidence

- a valid adopted video remains `VIDEO=READY` after a newer failed action.
- an obsolete-policy PASS is exposed as no current QC; adding a current-policy PASS qualifies the same selected media.
- a stale canonical variant reports `WORKING_MEDIA_STALE`, while unrelated historical stale candidates do not contaminate the working lineage.
- automation candidate reuse and workspace candidate counts share the same verified/non-stale set.
- current compose preflight already fingerprints timeline revision/hash, selected video SHA, audio bindings, subtitle revision, production geometry, and renderer contract, and verifies the render file hash before reuse.

## Verification

- 122 unique tests across episode actions, production read/run modes, generation variants, and G4/G6 QC: PASS before the final exact-render reuse tightening.
- 64 directly affected production read/run/mode tests after exact-render tightening: PASS.
- changed Python files: Ruff PASS.
- `git diff --check`: PASS (line-ending advisory warnings only).

## Boundary

K19 owns separating full aggregate counts from paged detail and director navigation windows. K20 owns contact-sheet consumption of the same current working slot. K18 preserves all immutable history and does not mutate production data while reading qualification.
