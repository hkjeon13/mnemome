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
    plan_policy = str(overlay.get("plan_policy") or "").strip()
    plan_template = str(prompt_template.get("plan") or "")
    if not plan_policy or PLAN_PROMPT_SPLIT_MARKER not in plan_template:
        raise RuntimeError("Mnemome prompt overlay or Lotte Agent plan marker is missing")
    prompt_template["plan"] = plan_template.replace(
        PLAN_PROMPT_SPLIT_MARKER,
        f"{plan_policy}\n\n---\n\n{PLAN_PROMPT_SPLIT_MARKER}",
        1,
    )
    return prompt_template
