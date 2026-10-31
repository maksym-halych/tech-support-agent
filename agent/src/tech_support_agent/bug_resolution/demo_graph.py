"""Separate public entry point for already-classified bugs, not general support."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.pregel import Pregel

from tech_support_agent.bug_resolution.adapters.private_api import PrivateAPI
from tech_support_agent.bug_resolution.bridge import (
    BridgeState,
    PublicBridge,
    PublicInput,
    normalize_messages,
)
from tech_support_agent.config import BugSettings


def create_demo_graph(
    api: PrivateAPI, checkpointer: BaseCheckpointSaver | None = None
) -> Pregel:
    """Compile only the public bridge; Agent Server supplies its own checkpointer."""

    graph = StateGraph(BridgeState, input_schema=PublicInput)
    graph.add_node("normalize_messages", normalize_messages)
    graph.add_node("bug_bridge", PublicBridge(api))
    graph.add_edge(START, "normalize_messages")
    graph.add_edge("normalize_messages", "bug_bridge")
    graph.add_edge("bug_bridge", END)
    return graph.compile(checkpointer=checkpointer)


def build_agent() -> Pregel:
    """Load server-only settings, never from a message, model or runtime argument."""

    return create_demo_graph(PrivateAPI(BugSettings()))
