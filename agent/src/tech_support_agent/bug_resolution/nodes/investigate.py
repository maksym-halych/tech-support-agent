"""Real model investigation over read-only Docker tools with artifact provenance."""

import json
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from tech_support_agent.bug_resolution.adapters.sandbox import (
    DockerSandbox,
    ImageArtifact,
)
from tech_support_agent.bug_resolution.agents import StageLimits, execute_stage
from tech_support_agent.bug_resolution.contracts import (
    CaseSnapshot,
    ImageAttachment,
    InvestigationResult,
)
from tech_support_agent.bug_resolution.prompts import INVESTIGATOR_PROMPT
from tech_support_agent.bug_resolution.sandbox.protocol import Navigate
from tech_support_agent.bug_resolution.tools.browser import browser_tools
from tech_support_agent.bug_resolution.tools.files import file_tools


async def investigate_case(
    case: CaseSnapshot,
    sandbox: DockerSandbox,
    model: BaseChatModel,
    directory: Path,
    limits: StageLimits,
    artifacts: dict[str, ImageArtifact],
) -> InvestigationResult:
    """Reproduce from a fresh page; only cite screenshots obtained during this stage."""

    await sandbox.request(Navigate(path="/"))
    data = {
        "report": case.request.report,
        "clarification": case.clarification.answer if case.clarification else None,
    }
    content = [{"type": "text", "text": json.dumps(data)}]
    attachments = [
        *case.request.attachments,
        *(case.clarification.attachments if case.clarification else []),
    ]
    for attachment in attachments:
        if isinstance(attachment, ImageAttachment):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{attachment.mime_type};base64,{attachment.data}"
                    },
                }
            )
        else:
            content.append({"type": "text", "text": attachment.text})
    run = await execute_stage(
        model,
        INVESTIGATOR_PROMPT,
        [HumanMessage(content=content)],
        [*file_tools(sandbox), *browser_tools(sandbox, directory)],
        InvestigationResult,
        limits,
        lambda artifact: artifacts.__setitem__(artifact.artifact_id, artifact),
    )
    actual = {artifact.artifact_id for artifact in run.artifacts}
    if not set(run.output.artifact_ids).issubset(actual):
        raise ValueError("Model cited an artifact not obtained by its tools")
    if run.output.status == "confirmed" and not any(
        name in run.tool_names
        for name in ("inspect_page", "click", "select", "navigate")
    ):
        raise ValueError("Confirmation requires actual browser observations")
    return run.output
