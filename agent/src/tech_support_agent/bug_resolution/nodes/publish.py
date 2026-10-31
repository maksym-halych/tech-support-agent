"""Narrow private-graph glue; no edits to shared case state or attempt ownership."""

from typing import Literal, TypedDict

from langgraph.types import interrupt

from tech_support_agent.bug_resolution.publication import Publisher
from tech_support_agent.bug_resolution.publication_contracts import (
    CIObservation,
    CIWait,
    PublicationAuthority,
    PublicationError,
    PublicationRef,
    VerifiedExport,
)


class PublicationState(TypedDict, total=False):
    """Private subgraph channels to map from/to the coordinator's case snapshot."""

    publication_authority: PublicationAuthority
    verified_export: VerifiedExport
    publication: PublicationRef
    ci_observation: CIObservation


class PublicationNodes:
    """Injected nodes; keep publish/mark-ready separate from replayed wait nodes."""

    def __init__(self, publisher: Publisher) -> None:
        self.publisher = publisher

    async def publish(self, state: PublicationState) -> PublicationState:
        """Finish publication side effects before the graph checkpoints its wait."""

        publication = await self.publisher.publish(
            state["publication_authority"],
            state["verified_export"],
            state.get("publication"),
        )
        return {"publication": publication}

    async def mark_ready(self, state: PublicationState) -> PublicationState:
        """Return a truthful observation; only passed may route to team notification."""

        observation = await self.publisher.mark_ready(
            state["publication_authority"],
            state["verified_export"],
            state["publication"],
        )
        return {"ci_observation": observation}


def wait_for_ci(state: PublicationState) -> PublicationState:
    """Interrupt without HTTP, Git, messaging, polling, or any other side effect."""

    wait = CIWait(publication=state["publication"])
    observation = CIObservation.model_validate(interrupt(wait.model_dump(mode="json")))
    if observation.publication != wait.publication:
        raise PublicationError("Stale CI resume does not match the active wait")
    return {"ci_observation": observation}


def route_ci(
    state: PublicationState,
) -> Literal["wait_for_ci", "mark_ready", "retry_or_handoff", "blocked"]:
    """Route without modifying the case's global fix-attempt counter."""

    observation = CIObservation.model_validate(state["ci_observation"])
    if observation.publication != PublicationRef.model_validate(state["publication"]):
        raise PublicationError("CI observation belongs to an old publication")
    return {
        "pending": "wait_for_ci",
        "passed": "mark_ready",
        "failed": "retry_or_handoff",
        "blocked": "blocked",
    }[observation.status]
