"""Interactive local runner with durable conversations and explicit approval."""

import argparse
import asyncio
import json
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from tech_support_agent.config import Settings, get_settings
from tech_support_agent.graph import build_support_graph, create_chat_model
from tech_support_agent.journal import ActionJournal
from tech_support_agent.models import (
    CHECKPOINT_TYPES,
    ApiConfig,
    ApiCredentials,
    SupportContext,
)


async def run_chat(settings: Settings, thread_id: str) -> None:
    """Run or resume a conversation, prompting separately for each API write."""

    settings.support_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    settings.support_journal_path.parent.mkdir(parents=True, exist_ok=True)
    context = SupportContext(
        user_id=settings.support_user_id,
        timezone=settings.support_timezone,
        credentials=ApiCredentials(
            bearer_token=settings.support_api_token, api_keys=settings.support_api_keys
        ),
    )
    api_config = ApiConfig(
        openapi_path=settings.support_openapi_path,
        base_url=settings.support_api_base_url,
        idempotency_header=settings.support_idempotency_header,
    )
    journal = ActionJournal(settings.support_journal_path)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
    try:
        async with AsyncSqliteSaver.from_conn_string(
            str(settings.support_checkpoint_path)
        ) as checkpointer:
            checkpointer.serde = JsonPlusSerializer(
                allowed_msgpack_modules=CHECKPOINT_TYPES
            )
            graph = build_support_graph(
                create_chat_model(settings), api_config, checkpointer, journal=journal
            )
            snapshot = await graph.aget_state(config)
            if snapshot.values.get("owner_user_id") not in {None, context.user_id}:
                raise PermissionError("This conversation belongs to another user")
            print(
                f"Діалог: {thread_id}. /exit — вийти; для відновлення: support-chat --thread-id {thread_id}"
            )
            if snapshot.values.get("result"):
                print(snapshot.values["result"].message)
            while True:
                snapshot = await graph.aget_state(config)
                interrupts = [
                    item for task in snapshot.tasks for item in task.interrupts
                ]
                if interrupts:
                    approval = interrupts[0].value
                    print(f"\nПідтвердження: {approval['summary']}")
                    print(json.dumps(approval["request"], ensure_ascii=False, indent=2))
                    if approval.get("error"):
                        print(approval["error"])
                    answer = (
                        (await asyncio.to_thread(input, "approve / reject / exit: "))
                        .strip()
                        .lower()
                    )
                    if answer in {"exit", "/exit"}:
                        return
                    if answer not in {"approve", "reject"}:
                        continue
                    graph_input = Command(
                        resume={"action_id": approval["action_id"], "decision": answer}
                    )
                elif snapshot.next:
                    graph_input = None
                else:
                    text = (await asyncio.to_thread(input, "Ви: ")).strip()
                    if text == "/exit":
                        return
                    if not text:
                        continue
                    graph_input = {"messages": [HumanMessage(content=text)]}
                result = await graph.ainvoke(graph_input, config, context=context)
                if result.get("result"):
                    print(f"Агент: {result['result'].message}")
    finally:
        journal.close()


def main() -> None:
    """Read arguments and start the interactive Python demo."""

    parser = argparse.ArgumentParser(
        description="Chat with the OpenAPI support subgraph"
    )
    parser.add_argument("--thread-id", default=None)
    args = parser.parse_args()
    try:
        asyncio.run(run_chat(get_settings(), args.thread_id or str(uuid4())))
    except KeyboardInterrupt, EOFError:
        return
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
