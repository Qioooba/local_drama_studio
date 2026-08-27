from pathlib import Path

import pytest

from local_drama.application.worker_dispatch import WorkerExecution, WorkerJobDispatcher
from local_drama.domain.errors import DomainRuleError


def test_worker_dispatch_routes_exact_declared_type(tmp_path: Path) -> None:
    dispatcher = WorkerJobDispatcher({"CPU_TEST": lambda job, root: WorkerExecution("TEXT", f"{root.name}/{job['id']}")})

    result = dispatcher.execute({"id": "j1", "type": "CPU_TEST"}, tmp_path)

    assert result == WorkerExecution("TEXT", f"{tmp_path.name}/j1")
    assert dispatcher.supported_types == frozenset({"CPU_TEST"})


def test_worker_dispatch_rejects_unknown_type_without_fallback(tmp_path: Path) -> None:
    with pytest.raises(DomainRuleError) as error:
        WorkerJobDispatcher({}).execute({"type": "SHELL"}, tmp_path)

    assert error.value.code == "JOB_TYPE_UNSUPPORTED"
