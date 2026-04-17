"""Claude planning layer.

Given a snapshot of HomeOS state (physical + financial + operational) and a
goal, asks Claude for a plan conforming to validator.schema.ACTION_PLAN_SCHEMA.

The Validator is authoritative: anything Claude returns still has to pass
validate_plan before OpenCLAW executes it. The planner only produces a
candidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from api.config import settings
from validator.schema import ACTION_PLAN_SCHEMA


SYSTEM_PROMPT = """You are the Claude planning layer of HomeOS, a deterministic
household state operating system.

HomeOS has four domains worth of state: physical (inventory, storage,
procurement), financial (accounts, obligations, liquidity), and operational
(tasks, resources, schedule). Hard constraints enforced by HomeOS include:

- All Tier-1 financial obligations must be met.
- No future liquidity breach (per-account minimum buffer).
- No critical inventory depletion.
- No operational conflicts (resource overbooking, deadline breaches).

You do NOT execute anything. You produce a JSON action plan. A separate
Validator will reject unsafe or malformed plans; a separate Executor
(OpenCLAW) will run plans that pass validation.

Constraints on the plan:
- Respond with a single JSON object, nothing else (no prose, no markdown fence).
- Schema: {"actions": [{"id": str, "target": "home_assistant|ssh|api",
  "operation": one of {call_service,get_state,systemctl_status,
  systemctl_restart,docker_ps,docker_logs,journalctl_tail,read_file},
  "parameters": {...}, "expected_outcome": str,
  "risk_level": "low|medium|high", "requires_approval": bool}]}
- Prefer read-only operations (get_state, *_status, *_logs, journalctl_tail,
  read_file) unless the goal explicitly requires a state change.
- systemctl_restart is only permitted for services
  home-assistant, docker, mosquitto.
- Never include destructive commands, credential changes, network changes,
  or package installs.
"""


@dataclass
class PlannerResult:
    plan: dict
    raw_response: str
    model: str
    dry_run: bool


class PlannerError(Exception):
    pass


async def plan(
    *,
    goal: str,
    state_snapshot: dict[str, Any],
    model: str = "claude-opus-4-7",
    max_tokens: int = 4096,
    client: httpx.AsyncClient | None = None,
) -> PlannerResult:
    """Produce an action plan from Claude.

    When ANTHROPIC_API_KEY is not set, returns an empty plan in dry-run mode
    so the rest of the system loop is still exercisable locally.
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        return PlannerResult(
            plan={"actions": []},
            raw_response="",
            model=model,
            dry_run=True,
        )

    user_content = json.dumps(
        {
            "goal": goal,
            "state": state_snapshot,
            "schema": ACTION_PLAN_SCHEMA,
        }
    )
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if client is not None:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
    else:
        async with httpx.AsyncClient(timeout=60.0) as c:
            resp = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
    if resp.status_code >= 400:
        raise PlannerError(f"Claude API returned HTTP {resp.status_code}: {resp.text}")
    body = resp.json()

    text = _extract_text(body)
    plan_obj = _extract_json(text)
    if not isinstance(plan_obj, dict) or "actions" not in plan_obj:
        raise PlannerError(f"Claude response did not contain a plan: {text[:500]}")
    return PlannerResult(plan=plan_obj, raw_response=text, model=model, dry_run=False)


def _extract_text(body: dict) -> str:
    parts = body.get("content", [])
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            chunks.append(str(part.get("text", "")))
    return "".join(chunks).strip()


def _extract_json(text: str) -> Any:
    """Pull the first JSON object out of the response text."""
    text = text.strip()
    if text.startswith("```"):
        # strip a leading ```json fence if the model added one despite instructions
        text = text.split("```", 2)[-1]
        if text.lstrip().lower().startswith("json"):
            text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
