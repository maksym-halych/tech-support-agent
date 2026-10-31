from typing import Any

from tech_support_agent.config import Settings, get_settings


def defaults() -> Any:
    """Build Settings with the developer's .env ignored.

    The defaults are a property of the code, not of whatever happens to be in
    the local .env — reading it here makes these tests pass or fail depending on
    the machine.
    """

    return Settings(_env_file=None)


def test_default_model() -> None:
    assert defaults().agent_model == "openai/gpt-6.1-sol"


def test_model_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL", "openai/gpt-6-astra")

    assert Settings(_env_file=None).agent_model == "openai/gpt-6-astra"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_base_url_defaults_to_openrouter() -> None:
    assert defaults().openai_base_url == "https://openrouter.ai/api/v1"


def test_base_url_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    assert Settings(_env_file=None).openai_base_url == "https://api.openai.com/v1"


def test_the_api_key_is_kept_out_of_reprs(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-part")

    settings = Settings(_env_file=None)

    assert settings.openai_api_key.get_secret_value() == "sk-secret-part"
    assert "secret-part" not in repr(settings)
