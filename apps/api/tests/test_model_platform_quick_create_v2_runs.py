from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.quick_create_v2_runs import QuickCreateV2RunService


def test_v2_multi_stage_run_is_idempotent_and_never_writes_legacy_quick_generation_tables(database) -> None:
    service = QuickCreateV2RunService(database)

    created = service.create(mode="TEXT_TO_IMAGE_TO_VIDEO", prompt="雨夜的橘猫撑伞穿过街道", idempotency_key="quick-v2-run-1")
    replay = service.create(mode="TEXT_TO_IMAGE_TO_VIDEO", prompt="雨夜的橘猫撑伞穿过街道", idempotency_key="quick-v2-run-1")

    assert created.state == "PLANNED"
    assert replay.id == created.id
    assert replay.idempotent_replay is True
    with database.connect() as connection:
        v2_count = connection.execute("SELECT COUNT(*) FROM mp_quick_create_v2_runs").fetchone()[0]
        legacy_count = connection.execute("SELECT COUNT(*) FROM quick_generation_runs").fetchone()[0]
    assert v2_count == 1
    assert legacy_count == 0


def test_v2_multi_stage_run_rejects_idempotency_key_reuse_for_changed_prompt(database) -> None:
    service = QuickCreateV2RunService(database)
    service.create(mode="TEXT_TO_VIDEO", prompt="雨夜的橘猫", idempotency_key="quick-v2-run-2")

    with pytest.raises(DomainRuleError) as raised:
        service.create(mode="TEXT_TO_VIDEO", prompt="雪夜的白猫", idempotency_key="quick-v2-run-2")

    assert raised.value.code == "IDEMPOTENCY_KEY_REUSED"
