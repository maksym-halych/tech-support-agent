"""Tool: answers to frequently asked questions about using the app."""

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tech_support_agent.context import AgentContext, signed_in_user
from tech_support_agent.faq.search import search_faq as find_entries

NO_MATCH = (
    "No FAQ entry matches. Try other words once; if there is still no match, "
    "say you do not know and do not guess how the app works."
)


class SearchFaqInput(BaseModel):
    """Input for search_faq."""

    question: str = Field(
        min_length=1,
        max_length=300,
        description="The user's question, rephrased in English, e.g. 'how to cancel a booking'",
    )


class _Answer(BaseModel):
    question: str
    answer: str


class _Answers(BaseModel):
    answers: list[_Answer]
    note: str | None = None


@tool(args_schema=SearchFaqInput)
def search_faq(question: str, runtime: ToolRuntime[AgentContext]) -> str:
    """
    Search the app's FAQ for how to do something, what a message or badge means, or why something does not work.

    Answer from the returned entries, and tell the user which buttons to click.

    Returns:
        JSON with the best matching questions and answers, best first.
    """

    user = signed_in_user(runtime.server_info)
    entries = find_entries(question, user.role if user else None)
    return _Answers(
        answers=[_Answer(question=e.question, answer=e.answer) for e in entries],
        note=None if entries else NO_MATCH,
    ).model_dump_json(exclude_none=True)
