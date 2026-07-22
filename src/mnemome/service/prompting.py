from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPT_OVERLAY_PATH = Path(__file__).with_name("prompts.yaml")


@lru_cache(maxsize=1)
def build_demo_prompt_template() -> dict[str, Any]:
    """Layer Mnemome policy onto Lotte Agent's installed default YAML template."""
    import yaml
    from lotte_agent.agents.toolcall.prompt_builders import PLAN_PROMPT_SPLIT_MARKER
    from lotte_agent.agents.toolcall.prompt_template_registry import resolve_prompt_template

    prompt_template = dict(resolve_prompt_template(None))
    overlay = yaml.safe_load(PROMPT_OVERLAY_PATH.read_text(encoding="utf-8")) or {}
    policies = {
        key: str(overlay.get(key) or "").strip()
        for key in ("plan_policy", "step_policy", "final_policy")
    }
    plan_template = str(prompt_template.get("plan") or "")
    step_template = str(prompt_template.get("step") or "")
    actual_task_marker = "Now, there is the actual task:"
    step_marker = f"---\n{actual_task_marker}"
    step_metadata_marker = "Metadata: {{metadata}}"
    step_response_marker = "Input: {{input}}\nResponse:"
    if (
        not all(policies.values())
        or PLAN_PROMPT_SPLIT_MARKER not in plan_template
        or step_marker not in step_template
        or step_metadata_marker not in step_template
        or step_response_marker not in step_template
    ):
        raise RuntimeError("Mnemome prompt overlay or Lotte Agent plan marker is missing")
    prompt_template["plan"] = plan_template.replace(
        PLAN_PROMPT_SPLIT_MARKER,
        f"{policies['plan_policy']}\n\n---\n\n{PLAN_PROMPT_SPLIT_MARKER}",
        1,
    )
    step_template = step_template.replace(
        step_metadata_marker,
        """{% if tool == 'no_tool' or tool.startswith('final_answer') %}
Metadata: {{metadata}}
{% else %}
Runtime Metadata:
- current_date={{metadata.current_date | default('')}}
- current_datetime={{metadata.current_datetime | default('')}}
- timezone={{metadata.timezone | default('')}}
Mnemome preferences are intentionally unavailable in tool execution steps.
Use only Input for the target scope.
{% endif %}""",
        1,
    )
    step_template = step_template.replace(
        step_response_marker,
        """Input: {{input}}
{% if tool == 'no_tool' or tool.startswith('final_answer') %}
Final response policy:
"""
        + policies["final_policy"]
        + """
{% endif %}
Response:""",
        1,
    )
    prompt_template["step"] = step_template.replace(
        step_marker,
        f"{policies['step_policy']}\n\n---\n{actual_task_marker}",
        1,
    )
    prompt_template["final_instruction"] = (
        f"{policies['final_policy']}\n\n"
        f"{str(prompt_template.get('final_instruction') or '')}"
    )
    for key in ("replan", "plan_repair"):
        prompt_template[key] = (
            f"{policies['plan_policy']}\n\n---\n\n"
            f"{str(prompt_template.get(key) or '')}"
        )
    return prompt_template
