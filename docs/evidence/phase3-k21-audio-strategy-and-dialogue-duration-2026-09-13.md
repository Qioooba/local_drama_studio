# Phase 3 / K21 evidence — frozen audio strategy and dialogue duration

Date: 2026-09-13 (Asia/Shanghai)

Scope: offline/local-media tests only. No new model installation, source separation, lip-sync model, GPU generation, or production mutation.

## Reproduced risks

- `tts_enabled` changed the workflow nodes but did not freeze an audio policy into the workflow/task/timeline identity.
- timeline assembly could proceed when dialogue had no current TTS selection, no verified media, or no measured duration because `dialogue_missing` was advisory only.
- active whole-dialogue bindings and per-line TTS could both enter one timeline, allowing the same spoken content to play twice.
- an implicit no-TTS path had no durable evidence that source/native audio must not be restored later.

## Implemented contract

- each production workflow and every task payload freezes `audio_strategy`:
  - `EXTERNAL_TTS` when the creator enables dialogue/subtitle generation;
  - `SILENT` when disabled.
- automation passes that frozen strategy into timeline assembly, and the frozen timeline input snapshot records it.
- `SILENT` adds neither active audio bindings nor per-line TTS and never falls back to a video's mixed/source audio.
- `EXTERNAL_TTS` requires every canonical dialogue line to have a current selection, verified audio, and positive measured duration.
- selected TTS duration defines the actual dialogue item range; the existing shared `dialogue_timing_issues` gate blocks any word-tail overrun rather than trimming it.
- per-line TTS plus an active whole-dialogue binding is an explicit `DIALOGUE_AUDIO_SOURCE_CONFLICT`, not an automatic double mix.
- H3 workflow handling still refuses to remove required Audio VAE nodes when native audio is mandatory; no false “audio off supported” rewrite was added.

## Acceptance evidence

- workflow tests prove EXTERNAL_TTS and SILENT are frozen into node metadata and all task payloads; SILENT omits TTS/subtitle jobs.
- a timeline plan with an adopted TTS candidate produces one DIALOGUE item under EXTERNAL_TTS and zero under SILENT.
- a 3.5-second measured line in a 2-second shot is blocked with a 1.5-second overrun; a 1.5-second line is preserved once without truncation.
- exact audio binding/media/subtitle inputs remain part of timeline and compose fingerprints, so changing a voice version requires timeline/compose revalidation.

## Verification

- 46 unique tests across audio requirements, H3 workflow audio handling, dialogue timing, timeline refresh/assembly, and episode runs: PASS.
- changed Python files: Ruff PASS.
- `git diff --check`: PASS (line-ending advisory warnings only).

## Boundary

The only production-run choices currently exposed are external TTS and silent. Native dialogue, source-audio reuse, or separating vocals from a mixed track require explicit future product/UI support and real evidence; they are not inferred here.
