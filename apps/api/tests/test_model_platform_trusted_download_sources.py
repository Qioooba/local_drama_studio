from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.trusted_download_sources import validate_trusted_https_source


def test_trusted_download_source_requires_an_explicit_exact_machine_host(workspace) -> None:
    configured = workspace.model_copy(update={"model_download_source_hosts": ("models.example.test",)})

    source = validate_trusted_https_source(configured, "https://models.example.test/releases/model.bin")

    assert source.host == "models.example.test"
    with pytest.raises(DomainRuleError) as untrusted:
        validate_trusted_https_source(configured, "https://cdn.models.example.test/releases/model.bin")
    assert untrusted.value.code == "MP_DOWNLOAD_SOURCE_UNTRUSTED"


@pytest.mark.parametrize("url", [
    "http://models.example.test/model.bin",
    "https://user@models.example.test/model.bin",
    "https://models.example.test:8443/model.bin",
    "https://models.example.test/model.bin?token=secret",
])
def test_trusted_download_source_rejects_unsafe_url_shapes(workspace, url: str) -> None:
    configured = workspace.model_copy(update={"model_download_source_hosts": ("models.example.test",)})

    with pytest.raises(DomainRuleError) as rejected:
        validate_trusted_https_source(configured, url)

    assert rejected.value.code == "MP_DOWNLOAD_SOURCE_INVALID"
