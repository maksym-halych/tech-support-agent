"""Agent graph assembly."""

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.pregel import Pregel

from tech_support_agent.config import Settings, get_settings
from tech_support_agent.context import AgentContext
from tech_support_agent.middleware import lesson_prompt, tool_call_limit
from tech_support_agent.tools.registry import TOOLS

# OpenAI's own endpoint, as opposed to another OpenAI-compatible provider.
OPENAI_BASE_URL = "https://api.openai.com/v1"


def create_lessons_agent(model: str | BaseChatModel) -> Pregel:
    """
    Assemble the lesson assistant around a model.

    Args:
        model: A ``provider:model`` string or a chat model instance.

    Returns:
        The compiled agent.
    """

    return create_agent(
        model=model,
        tools=TOOLS,
        middleware=[lesson_prompt, tool_call_limit()],
        context_schema=AgentContext,
        name="lessons_agent",
    )


def create_chat_model(settings: Settings) -> BaseChatModel:
    """
    Build the chat model the agent runs on, from any OpenAI-compatible provider.

    OpenAI itself is called over its Responses API, which its reasoning models
    need to call tools. Other compatible providers such as OpenRouter implement
    the chat completions API but not all of the Responses API, so they get chat
    completions.

    Args:
        settings: The settings naming the model, key and endpoint.

    Returns:
        The chat model.

    Raises:
        ValueError: If the API key is unset.
    """

    if not settings.openai_api_key.get_secret_value():
        raise ValueError(
            "OPENAI_API_KEY is not set: add it to agent/.env "
            "(see agent/.env.example) and restart the server."
        )

    return ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        use_responses_api=settings.openai_base_url.rstrip("/") == OPENAI_BASE_URL,
    )


def build_agent() -> Pregel:
    """
    Build the compiled agent graph served by Agent Server.

    Returns:
        The compiled agent, wired with the configured model, prompt and tools.
    """

    return create_lessons_agent(create_chat_model(get_settings()))
