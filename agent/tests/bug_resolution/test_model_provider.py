from pathlib import Path

import pytest
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from tech_support_agent.bug_resolution.agents import create_bug_model
from tech_support_agent.config import InvestigationSettings


def configured(tmp_path: Path, **overrides: object) -> InvestigationSettings:
    values = {
        "model": "configured-model",
        "model_api_key": "test-key",
        "telegram_bot_token": "test-token",
        "telegram_user_id": 123,
        "telegram_chat_id": 123,
        "artifacts_dir": tmp_path.resolve(),
    }
    return InvestigationSettings(_env_file=None, **(values | overrides))


@pytest.mark.parametrize(
    ("endpoint", "responses"),
    [
        ("https://api.openai.com/v1", True),
        ("https://openrouter.ai/api/v1", False),
    ],
)
def test_explicit_provider_routing(
    tmp_path: Path, endpoint: str, responses: bool
) -> None:
    settings = configured(tmp_path, model_base_url=endpoint)
    model = create_bug_model(settings)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "configured-model"
    assert model.openai_api_base == endpoint
    assert model.openai_api_key.get_secret_value() == "test-key"
    assert model.use_responses_api is responses
    assert model.max_retries == 0
    assert model.request_timeout == settings.stage_seconds


def test_openai_is_default(tmp_path: Path) -> None:
    assert configured(tmp_path).model_base_url == "https://api.openai.com/v1"


def test_unknown_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        configured(tmp_path, model_base_url="https://untrusted.example/v1")


def test_dotenv_can_explicitly_reuse_openai_settings(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_API_KEY=test-key\n"
        "AGENT_MODEL=configured-model\n"
        "BUG_MODEL=${AGENT_MODEL}\n"
        "BUG_MODEL_API_KEY=${OPENAI_API_KEY}\n"
        "BUG_MODEL_BASE_URL=https://api.openai.com/v1\n"
        "BUG_TELEGRAM_BOT_TOKEN=test-token\n"
        "BUG_TELEGRAM_USER_ID=123\n"
        "BUG_TELEGRAM_CHAT_ID=123\n"
        f"BUG_ARTIFACTS_DIR={tmp_path.resolve()}\n"
    )
    settings = InvestigationSettings(_env_file=env)
    assert settings.model == "configured-model"
    assert settings.model_api_key.get_secret_value() == "test-key"
    assert settings.model_base_url == "https://api.openai.com/v1"
