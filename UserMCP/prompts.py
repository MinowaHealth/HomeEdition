"""
MCP prompts exposed by UserMCP.

Prompts are slash-command-style templates the client can invoke to kick
off a multi-tool workflow. The server does not call tools on the user's
behalf — it returns an instruction message that tells the LLM which
tools to call and in what order.

Two prompts are published:

  /visit-prep [provider?]  — assemble a visit-prep packet
  /weekly-check-in         — 7-day rollup across wearables, vitals,
                             adherence, and food

Keeping the instructions here (not in the LLM's system prompt) means a
client running against a different model still gets the same workflow,
and the sequence can be updated without shipping a new client build.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent


_VISIT_PREP_TEMPLATE = """\
You are helping the user prepare for an upcoming appointment{provider_clause}.

Produce a concise visit-prep packet. Call these tools in order, then
synthesize the output — do not paste raw tool responses.

1. `get_my_profile` — confirm identity, timezone, and active profile
   details.
2. `get_my_active_regimen` — list currently active medications and
   supplements. Note doses per day and scheduling.
3. `get_vitals_timeline` with `days=30` — surface recent blood pressure,
   weight, temperature trends. Flag any single reading or pattern that
   appears clinically noteworthy (sustained BP ≥140/90, weight change
   >2kg in 30 days, etc.).
4. `get_lab_history` — list the most recent result for each available
   test. Call out any flagged-high/low values.
5. `get_my_clinical_history` — surface active conditions, allergies, and
   any `alerts` (med/allergen overlap) returned in the envelope.
6. `get_recent_activity` with `days=14` — scan for patient-reported
   observations the clinician should see.

Output sections, in order:
  - **Who you are** (name, pronouns, timezone, 1-line profile)
  - **Current regimen** (bulleted meds + supplements with dose/frequency)
  - **Vitals snapshot** (last reading + 30-day range for BP, weight,
    temperature; call out gaps if a source is cold)
  - **Recent labs** (latest value per test, flag abnormals)
  - **Clinical history** (active conditions, allergies, alerts)
  - **Concerns to raise** (user-reported symptoms, adherence issues,
    observations from the last 14 days the user may want to mention)

Every tool response includes a `coverage` and `sources` block. If any
source is stale or missing (e.g. Garmin never synced), say so in the
relevant section rather than omitting it silently. End with the standard
disclaimer from the envelope."""


_WEEKLY_CHECK_IN_TEMPLATE = """\
Produce a 7-day check-in for the user. Call these tools in parallel
where possible, then present a single narrative answer.

Tools to call (all with `days=7`):
1. `get_wearable_summary` — Garmin + HealthKit rollup (steps, resting
   HR, sleep, stress). If no wearable is connected, say so and skip
   this section.
2. `get_vitals_timeline` with `include: [bp, weight, temperature]` —
   manually entered vitals.
3. `get_adherence_report` — scheduled-input doses logged vs. expected.
4. `get_nutrition_report` with `days=7` — daily calorie/macro rollup
   and any dietary-setting violations.

Output as four short sections with one-line headlines each, followed by
a single "What to keep an eye on" paragraph (no more than 3 sentences).

Rules:
  - Lead each section with the headline number (e.g. "Steps: avg
    7,430/day, down 12% vs. last week"), not a paragraph of prose.
  - If `coverage.gaps` contains an entry for a section's source, flag
    it rather than presenting the rollup as complete.
  - End with the envelope's disclaimer verbatim."""


def _visit_prep_result(provider: Optional[str]) -> GetPromptResult:
    provider_clause = f" with {provider}" if provider else ""
    text = _VISIT_PREP_TEMPLATE.format(provider_clause=provider_clause)
    return GetPromptResult(
        description="Assemble a visit-prep packet across regimen, vitals, labs, and history.",
        messages=[
            PromptMessage(role="user", content=TextContent(type="text", text=text)),
        ],
    )


def _weekly_check_in_result() -> GetPromptResult:
    return GetPromptResult(
        description="Produce a 7-day rollup across wearables, vitals, adherence, and food.",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=_WEEKLY_CHECK_IN_TEMPLATE),
            ),
        ],
    )


def all_prompts() -> List[Prompt]:
    """Entry point for `mcp_server.list_prompts`."""
    return [
        Prompt(
            name="visit-prep",
            description=(
                "Assemble a visit-prep packet: active regimen, recent vitals, "
                "recent labs, open concerns, allergy warnings. Optional "
                "`provider` argument scopes the framing to one clinician."
            ),
            arguments=[
                PromptArgument(
                    name="provider",
                    description="Optional provider name to frame the prep around.",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="weekly-check-in",
            description=(
                "7-day rollup across wearables, vitals, adherence, and food, "
                "presented as a single narrative answer."
            ),
            arguments=[],
        ),
    ]


async def get(name: str, arguments: Optional[Dict[str, Any]] = None) -> GetPromptResult:
    """Entry point for `mcp_server.get_prompt`."""
    arguments = arguments or {}
    if name == "visit-prep":
        return _visit_prep_result(arguments.get("provider"))
    if name == "weekly-check-in":
        return _weekly_check_in_result()
    raise ValueError(f"Unknown prompt: {name}")
