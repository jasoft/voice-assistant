from press_to_talk.storage.models import StorageConfig
from press_to_talk.storage.service import StorageService, load_storage_config


def test_mem0_backend_uses_configured_soj_identity(monkeypatch):
    monkeypatch.setenv("PTT_USER_ID", "wangwei")
    monkeypatch.setenv("MEM0_USER_ID", "wangwei")
    monkeypatch.delenv("PTT_REMEMBER_BACKEND", raising=False)

    config = load_storage_config()

    assert config.backend == "mem0"
    assert config.mem0_user_id == "soj"


def test_mem0_storage_service_does_not_inherit_api_identity():
    service = StorageService(
        StorageConfig(backend="mem0", user_id="wangwei", mem0_user_id="wangwei"),
        use_cli=False,
    )

    assert service.config.user_id == "wangwei"
    assert service.config.mem0_user_id == "soj"
