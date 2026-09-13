# Phase 3 / K15 evidence — shared generation inputs and bounded graph compilation

Date: 2026-09-13 (Asia/Shanghai)

Scope: offline repository/application tests only. No real model request, GPU render, model download, production database write, or contact with the running service was performed.

## Reproduced risks

- `ComfyGenerationService._apply_effective_configuration` searched the complete graph for common input names (`steps`, `cfg`, `width`, `height`, and others). A second sampler or a reference-scaling node could therefore receive an override that its published Workflow binding never authorized.
- the Shot Studio route and episode automation route both reached `GenerationService`, but automation supplied a raw prompt without the canonical PromptBundle negative-policy compile.
- preflight did not expose one complete structure containing the final model inputs, ordered immutable media references, hashes/probe facts, and timing distinctions.
- the H3 tier implementation could be read as an authority for story length even though narrative duration, legal H3 frames, predicted file duration, and timeline use range are different facts.

## Implemented contract

- `GenerationService._actual_execution_inputs` is now the shared server-side compilation result for both entry points. It freezes:
  - final semantic inputs and the exact subset declared by the published Workflow;
  - style/brand and character-anchor additions;
  - media role, ordinal, immutable MediaVersion id, SHA-256, integrity, kind, and probe facts;
  - a separate H3 timing snapshot.
- new jobs consume the same `compiled_semantic_inputs` returned by preflight. Unbound camera/control metadata remains auditable under `execution_metadata` and is not presented as a graph input.
- episode automation supplies the same PromptBundle compiler input as Shot Studio, including the canonical negative fallback, and keeps camera-plan metadata.
- `h3_timing_snapshot` freezes narrative target duration, legal `17k+5` frame count, fixed generation fps, predicted render duration, timeline use range, and an initially unknown measured output duration. Production tier does not alter the narrative target.
- the effective-configuration snapshot freezes the Profile's setting-to-semantic-role map. The Comfy worker applies scalar values only through published semantic bindings. Whole-graph scalar scanning was removed. Turbo-LoRA and native-audio remain narrowly scoped structural operations.
- duplicate role/ordinal bindings and Profile cardinality overflow fail during preflight, before Variant/Job writes.
- Shot Studio shows the last server-validated model inputs, media evidence, and timing evidence as a read-only execution preview.

## Acceptance evidence

- seed `0` remains a valid explicit value through preflight and the frozen job.
- 16:9 H3 timing test: a 4,000 ms narrative target freezes 107 frames at 24 fps, predicted render duration 4,458 ms, and timeline use range 0–4,000 ms.
- single-shot and automation-shaped plans produce byte-equal compiled model inputs and timing even when automation carries `DURATION_SECONDS` and a production tier as metadata/override intent.
- explicit first/end-frame roles remain independent and existing aspect-ratio/probe gates pass.
- duplicate and overflow references are rejected before writes; the immutable media evidence retains role/ordinal/id/SHA/probe.
- a sentinel graph with a primary sampler, second sampler, reference ImageScale node, and H3 generation node proves that only explicitly bound fields change.
- FL2VA/Ref2VA and H3 tier factory regression suites pass; no generic tail crop was introduced and native audio/end constraints remain intact.
- generated OpenAPI/client artifacts include `actual_execution_inputs`.

## Verification run

- 94 unique backend tests across generation planning/variants, style and character prompt injection, Comfy jobs, H3 overrides/tiers, Ref2VA, and episode worker actions: PASS.
- `ShotGenerationInspector.test.tsx`: 10 tests PASS.
- changed Python files: Ruff PASS.
- `git diff --check`: PASS (Git emitted existing LF/CRLF advisory warnings only).
- web build reaches the repository's known unrelated TypeScript error at `LocalLLMConfigurationPanel.tsx:181` after the K15 test passes; K15 introduces no additional TypeScript error.

## Remaining boundary

Real-media measured output duration remains populated by the existing media probe/result-promotion path, not guessed at plan time. No L3 GPU sample was authorized, so this is an offline K15 completion and not a claim of real H3 visual/audio quality.
