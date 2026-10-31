import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from tech_support_agent.faq.search import load_faq, search_faq, terms
from tech_support_agent.graph import create_lessons_agent
from tests.conftest import as_student, as_teacher, call, scripted_model


def questions(entries) -> list[str]:
    return [entry.question for entry in entries]


def test_every_entry_has_an_audience_keywords_and_an_answer() -> None:
    entries = load_faq()

    assert len(entries) >= 20
    assert len(set(questions(entries))) == len(entries)
    for entry in entries:
        assert entry.keywords, entry.question
        assert "Audience:" not in entry.answer
        assert "Keywords:" not in entry.answer


def test_a_bad_audience_is_refused(tmp_path: Path) -> None:
    faq = tmp_path / "faq.md"
    faq.write_text("# FAQ\n\n## Question?\n\nAudience: admins\n\nAnswer.\n")

    with pytest.raises(ValueError):
        load_faq(faq)


def test_terms_drop_stopwords_and_stem() -> None:
    assert terms("How do I cancel my bookings?") == {"cancel", "book"}


def test_student_finds_how_to_book() -> None:
    found = search_faq("how to book a lesson", "student")

    assert found[0].question == "How do I book a lesson?"


def test_teacher_finds_how_to_open_a_lesson() -> None:
    found = search_faq("how do I create a new lesson slot", "teacher")

    assert found[0].question == "How do I open a lesson for students?"


def test_an_error_message_finds_its_entry() -> None:
    found = search_faq("Someone else has already booked this lesson", "student")

    assert found[0].question == 'It says "Someone else has already booked this lesson"'


def test_each_role_sees_only_its_entries() -> None:
    student = {entry.audience for entry in search_faq("cancel", "student", limit=50)}
    teacher = {entry.audience for entry in search_faq("cancel", "teacher", limit=50)}

    assert student <= {"everyone", "student"}
    assert teacher <= {"everyone", "teacher"}
    assert questions(search_faq("how do I cancel", "teacher"))[0] == (
        "How do I cancel a lesson?"
    )


def test_nobody_signed_in_searches_every_entry() -> None:
    audiences = {entry.audience for entry in search_faq("cancel", None, limit=50)}

    assert audiences == {"everyone", "student", "teacher"}


def test_an_unrelated_question_finds_nothing() -> None:
    assert search_faq("what is the weather", "student") == []


async def run_search(config, question: str) -> dict:
    model = scripted_model(call("search_faq", question=question), AIMessage("Done"))
    agent = create_lessons_agent(model, InMemorySaver())
    result = await agent.ainvoke(
        {"messages": [HumanMessage("Help")]}, config, context={"timezone": "UTC"}
    )
    (message,) = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    return json.loads(message.content)


async def test_the_tool_answers_for_the_role() -> None:
    student = await run_search(as_student(), "how do I become a teacher")
    teacher = await run_search(as_teacher(), "how do I become a teacher")

    assert student["answers"][0]["question"] == "How do I become a teacher?"
    assert "How do I become a teacher?" not in [
        answer["question"] for answer in teacher["answers"]
    ]


async def test_the_tool_says_when_nothing_matches() -> None:
    result = await run_search(as_student(), "what is the weather")

    assert result["answers"] == []
    assert "do not guess" in result["note"]
