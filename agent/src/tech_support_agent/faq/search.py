"""Load the FAQ from ``faq.md`` and find the entries that match a question."""

import re
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FAQ_PATH = Path(__file__).with_name("faq.md")

Audience = Literal["everyone", "student", "teacher"]

# Words that say nothing about which entry a question is about. "lesson" is in
# almost every entry, so it would only add noise to the score.
STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from get how i if in is it "
    "me my no not of on or so that the there this to was what when where which "
    "who why will with you your lesson app".split()
)

# A question term weighs most in the entry's question, then in its keywords.
QUESTION_WEIGHT = 3
KEYWORDS_WEIGHT = 2
ANSWER_WEIGHT = 1

_SUFFIXES = ("ing", "ed", "es", "s")
_METADATA = re.compile(r"^(Audience|Keywords):\s*(.*)$")


class FaqEntry(BaseModel):
    """One question and its answer."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1)
    audience: Audience
    keywords: tuple[str, ...] = ()
    answer: str = Field(min_length=1)


def _stem(word: str) -> str:
    # Two rounds turn "bookings" into "book"; then "cancell" (from "cancelled")
    # loses its doubled letter to match "cancel". Queries and entries go
    # through the same steps, so an odd stem still matches itself.
    for _ in range(2):
        suffix = next(
            (s for s in _SUFFIXES if word.endswith(s) and len(word) - len(s) >= 3),
            None,
        )
        if suffix is None:
            break
        word = word[: -len(suffix)]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] != "s":
        word = word[:-1]
    return word


def terms(text: str) -> frozenset[str]:
    """
    Split text into the lowercase, roughly stemmed words a search compares.

    Args:
        text: Any text.

    Returns:
        The distinct terms, without stopwords.
    """

    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(_stem(word) for word in words if word not in STOPWORDS)


def _parse_entry(section: str) -> FaqEntry:
    heading, _, body = section.partition("\n")
    metadata: dict[str, str] = {}
    answer: list[str] = []
    for line in body.strip().splitlines():
        match = _METADATA.match(line)
        if match and not answer:
            metadata[match.group(1).lower()] = match.group(2).strip()
        else:
            answer.append(line)
    keywords = metadata.get("keywords", "")
    return FaqEntry(
        question=heading.strip(),
        audience=metadata.get("audience", ""),
        keywords=tuple(word.strip() for word in keywords.split(",") if word.strip()),
        answer="\n".join(answer).strip(),
    )


@cache
def load_faq(path: Path = FAQ_PATH) -> tuple[FaqEntry, ...]:
    """
    Read the FAQ: each ``## `` heading is a question, followed by its metadata and answer.

    Args:
        path: The Markdown file.

    Returns:
        The entries, in file order.

    Raises:
        ValueError: If an entry has no answer or an unknown audience.
    """

    sections = re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    # The text before the first question describes the file.
    return tuple(_parse_entry(section) for section in sections[1:])


def _score(query: frozenset[str], entry: FaqEntry) -> int:
    return (
        QUESTION_WEIGHT * len(query & terms(entry.question))
        + KEYWORDS_WEIGHT * len(query & terms(" ".join(entry.keywords)))
        + ANSWER_WEIGHT * len(query & terms(entry.answer))
    )


def search_faq(question: str, role: str | None, limit: int = 3) -> list[FaqEntry]:
    """
    Find the FAQ entries that best match a question.

    Args:
        question: The user's question, in English.
        role: ``student`` or ``teacher``; None (nobody signed in) searches
            every entry.
        limit: The most entries to return.

    Returns:
        The matching entries, best first; empty if no entry shares a term.
    """

    query = terms(question)
    entries = [
        entry
        for entry in load_faq()
        if role is None or entry.audience in ("everyone", role)
    ]
    scored = [(score, entry) for entry in entries if (score := _score(query, entry))]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in scored[:limit]]
