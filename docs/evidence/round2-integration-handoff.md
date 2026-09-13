# Second-round integration handoff

Date: 2026-09-13

This snapshot contains only verified second-round product fixes. The broader K01-K30 / V01-V54 UAT remains active; this document does not claim final completion.

## Snapshot scope

Audio requirement freshness and stale-binding prevention:

- `apps/api/local_drama/application/audio_requirements.py`
- `apps/api/local_drama/infrastructure/database/shot_studio_repository.py`
- `apps/api/local_drama/api/schemas/shot_studio.py`
- `apps/web/src/generated/api.ts`
- `apps/web/src/features/director-v2/DirectorSoundInspector.tsx`
- `apps/web/src/features/director-v2/DirectorSoundInspector.test.tsx`
- `scripts/generate_client.py`
- `apps/api/tests/test_audio_requirements.py`
- `apps/api/tests/test_shot_dialogue_v2.py`

Windows SAPI voice selection and synthesis:

- `apps/api/local_drama/platform/windows/tts.py`
- `apps/api/tests/test_local_sapi_tts.py`

Cancellation propagation through episode production and automation workflows:

- `apps/api/local_drama/infrastructure/database/episode_production_command_repository.py`
- `apps/api/local_drama/application/automation_workflows.py`
- `apps/api/tests/test_episode_production_v2.py`
- `apps/api/tests/test_automation_workflows.py`

## Verification completed before snapshot

- Backend K22/K29-focused run: 68 tests, 0 failures, 0 errors, 0 skips. Machine-readable result: `work/uat-second-round/logs/round2-k22-k29-focused.xml`.
- Focused web run: 38 tests, 0 failures, 0 errors. Machine-readable result: `work/uat-second-round/logs/round2-web-focused.xml`.
- Earlier focused checks covering the new backend behavior and Director sound UI: 7 backend tests passed and 7 web tests passed.
- Windows SAPI-focused tests passed.
- `git diff --check` completed without whitespace errors; only line-ending warnings were reported.

Runtime checks also confirmed that cancellation is propagated without stopping shared GPU services, stale voice candidates are not adoptable in the Director UI, and a shot-scoped new Take remains local to its requested shot. No shared production API, ComfyUI, or Ollama service was stopped.

## Integration notes

- The worktree started detached from `e7b7ec8`; the integration branch is `codex/round2-verified-fixes`.
- The snapshot commit is the commit containing this handoff file; its exact hash is supplied to the integration task when the commit is created.
- Runtime databases, generated media, logs, configuration, model manifests, credentials, and unrelated first-round changes are intentionally excluded.

## Work still in progress

- Minimal real I2V and operation preview/execution coverage for retry/recompose scope.
- Restart recovery and two-episode isolation closure.
- Final K/V evidence matrix and one final machine-readable full regression run after the code is frozen.
