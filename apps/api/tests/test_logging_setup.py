import logging
from pathlib import Path

from local_drama.config import Settings
from local_drama.logging_setup import configure_logging


def test_logging_uses_process_role_and_pid_specific_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_LOG_FILE", "1")
    settings = Settings(logs_root=tmp_path)

    configure_logging(settings, process_role="api")
    configure_logging(settings, process_role="worker")
    logging.getLogger("local_drama.test").warning("role-safe")
    for handler in logging.getLogger("local_drama").handlers:
        handler.flush()

    names = sorted(path.name for path in tmp_path.glob("*.log"))
    assert len(names) == 2
    assert any(name.startswith("local_drama-api-") for name in names)
    assert any(name.startswith("local_drama-worker-") for name in names)
