'''debate.py – three‑person counterfactual debate for a compliance audit

This module wires a LangGraph that fans‑out three LLM agents (Realist, Optimist,
Pessimist) and then synthesises their perspectives into a single audit
conclusion.

The implementation follows the same style as the other backend modules:
- Pydantic models describe the structured LLM output (Instructor + Groq).
- TypedDict defines the mutable graph state.
- Each node is a pure function that receives the current state, calls the LLM
  and returns a partial state update.
- The graph is compiled once and reused by ``run_debate``.

The user will import ``run_debate`` and pass a ``DebateState`` dict containing
any pre‑computed information (evidence, versioning, calculations). The function
returns the final state, where ``synthesis`` holds the combined narrative.
'''

from __future__ import annotations

from typing import TypedDict, Literal, Dict, Any

from pydantic import BaseModel, Field
import instructor  # type: ignore  # instructor wraps the Groq client for structured output
import groq  # type: ignore  # Groq Python SDK
from langgraph.graph import StateGraph, START, END

# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class DebateState(TypedDict, total=False):
    """Mutable state that flows through the LangGraph.

    The keys are populated incrementally by the nodes. Only the keys that a
    node returns need to be present – LangGraph merges the dictionaries.
    """

    # Input data (populated upstream by ``orchestrator``)
    evidence: str | None
    version_info: str | None
    calculations: str | None
    findings: str | None

    # Earnings-opportunity mode. When ``scenario`` is present the three personas
    # assess that what-if scenario instead of reviewing the audit report.
    scenario: str | None

    # Agent outputs
    realist: str | None
    optimist: str | None
    pessimist: str | None

    # Agent error tracking (to distinguish complete vs incomplete debates)
    realist_error: str | None
    optimist_error: str | None
    pessimist_error: str | None

    # Final synthesis
    synthesis: str | None

# ---------------------------------------------------------------------------
# Structured LLM response models
# ---------------------------------------------------------------------------

class AgentResponse(BaseModel):
    """Base model for the three personas.

    * ``analysis`` – free‑form reasoning.
    * ``confidence`` – a float in ``[0, 1]`` indicating how certain the agent is.
    """

    analysis: str = Field(..., description="The agent's detailed reasoning about the audit")
    confidence: float = Field(..., ge=0, le=1, description="Confidence score between 0 and 1")

# The three personas share the same schema, so we can reuse ``AgentResponse``.

# ---------------------------------------------------------------------------
# LLM client – Groq with Instructor wrapper
# ---------------------------------------------------------------------------

# NOTE: The Groq API key is expected to be present in the environment variable
# ``GROQ_API_KEY``. The wrapper automatically extracts the key.

groq_client = groq.Groq()
# ``instructor`` decorates the client so that ``client.chat.completions.create``
# returns a Pydantic model instance instead of raw JSON.
#
# IMPORTANT: GPT-OSS through Groq doesn't support function calling (tools).
# We must use instructor.Mode.JSON for JSON-mode structured output instead
# of the default Mode.TOOLS which requires function calling.

instructor_client = instructor.from_groq(groq_client, mode=instructor.Mode.JSON)

# ---------------------------------------------------------------------------
# Prompt builders – the user already wrote these in the previous session.
# We keep the signatures and import them if they exist; otherwise we provide a
# very small fallback implementation to keep the file runnable.
# ---------------------------------------------------------------------------

# Shared wording for earnings-opportunity mode. The 3-bullet / 60-word limits
# match the audit prompts below.
_SCENARIO_RULES = (
    "CONSTRAINTS:\n"
    "- Maximum 3 bullet points\n"
    "- Maximum 60 words total\n"
    "- Simple English a delivery partner with no technical knowledge can read\n"
    "- Judge ONLY the what-if scenario and the figures supplied below\n"
    "- Do not review, summarise or repeat the past transaction or audit report\n"
    "- Use only the supplied numbers; never invent figures\n"
    "- Call projected earnings an estimate, never guaranteed income\n"
    "- No introductions or conclusions beyond the bullets"
)


def _scenario_details(state: DebateState) -> str:
    """Append only the scenario and its calculated figures (no audit content)."""
    details = []
    for key in ("scenario", "calculations"):
        if value := state.get(key):
            details.append(f"{key.replace('_', ' ').title()}: {value}\n")
    return "\n\n" + "".join(details)


def build_realist_prompt(state: DebateState) -> str:
    """Return a prompt for the Realist persona - concise synthesis."""
    if state.get("scenario"):
        return (
            "You are the REALIST advising a delivery partner about a what-if earnings "
            "scenario. Give a balanced assessment of the calculated result.\n\n"
            "OUTPUT FORMAT (strict):\n"
            "## Realist Assessment\n"
            "- Bullet 1: What the calculated financial impact actually is\n"
            "- Bullet 2: Which part is an assumption rather than a fact\n"
            "- Bullet 3: Balanced takeaway for the partner\n\n"
            + _SCENARIO_RULES
        ) + _scenario_details(state)
    base = (
        "You are the REALIST. Provide a concise, balanced synthesis of the audit findings.\n\n"
        "OUTPUT FORMAT (strict):\n"
        "## Realist Assessment\n"
        "- Bullet 1: Key fact or conclusion\n"
        "- Bullet 2: Key uncertainty or gap\n"
        "- Bullet 3: Actionable insight\n\n"
        "CONSTRAINTS:\n"
        "- Maximum 3 bullet points\n"
        "- Maximum 60 words total\n"
        "- No introductions, conclusions, or explanations beyond bullets\n"
        "- Distinguish verified facts from assumptions\n"
        "- Do not repeat points already made by Optimist/Pessimist\n"
        "- Focus on what the data actually shows"
    )
    details = []
    for key in ("evidence", "version_info", "calculations", "findings"):
        if value := state.get(key):
            details.append(f"{key.replace('_', ' ').title()}: {value}\n")
    return base + "\n\n" + "".join(details)


def build_optimist_prompt(state: DebateState) -> str:
    """Return a prompt for the Optimist persona - concise positive findings."""
    if state.get("scenario"):
        return (
            "You are the OPTIMIST advising a delivery partner about a what-if earnings "
            "scenario. Explain the potential benefits of this scenario.\n\n"
            "OUTPUT FORMAT (strict):\n"
            "## Optimist Assessment\n"
            "- Bullet 1: The main earnings benefit, using the calculated figures\n"
            "- Bullet 2: A second realistic upside of this change\n"
            "- Bullet 3: A practical way the partner could make it work\n\n"
            + _SCENARIO_RULES
        ) + _scenario_details(state)
    base = (
        "You are the OPTIMIST. Highlight only the most important positive findings.\n\n"
        "OUTPUT FORMAT (strict):\n"
        "## Optimist Assessment\n"
        "- Bullet 1: Most significant opportunity or favorable finding\n"
        "- Bullet 2: Second most important positive aspect\n"
        "- Bullet 3: Third positive point or potential benefit\n\n"
        "CONSTRAINTS:\n"
        "- Maximum 3 bullet points\n"
        "- Maximum 60 words total\n"
        "- No introductions, conclusions, or generic optimism\n"
        "- Focus on opportunities and potential benefits supported by data\n"
        "- Do not invent or exaggerate positive outcomes\n"
        "- Only cite what the evidence actually supports"
    )
    details = []
    for key in ("evidence", "version_info", "calculations", "findings"):
        if value := state.get(key):
            details.append(f"{key.replace('_', ' ').title()}: {value}\n")
    return base + "\n\n" + "".join(details)


def build_pessimist_prompt(state: DebateState) -> str:
    """Return a prompt for the Pessimist persona - concise risk findings."""
    if state.get("scenario"):
        return (
            "You are the PESSIMIST advising a delivery partner about a what-if earnings "
            "scenario. Explain the risks and limits of this scenario.\n\n"
            "OUTPUT FORMAT (strict):\n"
            "## Pessimist Assessment\n"
            "- Bullet 1: The main reason the estimate may not be reached\n"
            "- Bullet 2: A real cost or downside of this change, such as demand that "
            "varies by hour or higher fuel and vehicle costs, where relevant\n"
            "- Bullet 3: A limit of the data behind the estimate\n\n"
            + _SCENARIO_RULES + "\n"
            "- Do not invent losses, penalties or consequences the figures do not show"
        ) + _scenario_details(state)
    base = (
        "You are the PESSIMIST. Highlight only the most important risks.\n\n"
        "OUTPUT FORMAT (strict):\n"
        "## Pessimist Assessment\n"
        "- Bullet 1: Most significant risk or concern\n"
        "- Bullet 2: Second most important risk\n"
        "- Bullet 3: Third risk or data gap\n\n"
        "CONSTRAINTS:\n"
        "- Maximum 3 bullet points\n"
        "- Maximum 60 words total\n"
        "- No introductions, conclusions, or generic pessimism\n"
        "- Focus on actual risks supported by the available data\n"
        "- Do not invent hypothetical financial losses or legal consequences\n"
        "- Do not speculate beyond what the evidence shows\n"
        "- Only cite risks directly supported by the data"
    )
    details = []
    for key in ("evidence", "version_info", "calculations", "findings"):
        if value := state.get(key):
            details.append(f"{key.replace('_', ' ').title()}: {value}\n")
    return base + "\n\n" + "".join(details)

# ---------------------------------------------------------------------------
# Agent node functions – each returns a dict that updates the graph state.
# ---------------------------------------------------------------------------

async def realist_node(state: DebateState) -> Dict[str, Any]:
    """Run the Realist persona and store its output.

    The function builds the prompt, calls the LLM with ``AgentResponse`` as the
    output schema, and returns ``{"realist": response.analysis}``.
    """
    prompt = build_realist_prompt(state)
    try:
        result = instructor_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            response_model=AgentResponse,
            temperature=0.3,
        )
        # Handle both coroutine (needs await) and direct return (already resolved)
        if hasattr(result, '__await__'):
            response = await result
        else:
            response = result

        # Ensure we have a valid response
        if not isinstance(response, AgentResponse):
            error_msg = f"Realist agent error: Expected AgentResponse, got {type(response)}"
            return {"realist": f"[Error: {error_msg}]", "realist_error": error_msg}
        return {"realist": response.analysis, "realist_error": None}
    except Exception as e:
        error_msg = f"Realist agent error: {type(e).__name__} - {str(e)}"
        return {"realist": f"[Error: {error_msg}]", "realist_error": error_msg}


async def optimist_node(state: DebateState) -> Dict[str, Any]:
    """Run the Optimist persona and store its output."""
    prompt = build_optimist_prompt(state)
    try:
        result = instructor_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            response_model=AgentResponse,
            temperature=0.6,
        )
        # Handle both coroutine (needs await) and direct return (already resolved)
        if hasattr(result, '__await__'):
            response = await result
        else:
            response = result

        if not isinstance(response, AgentResponse):
            error_msg = f"Optimist agent error: Expected AgentResponse, got {type(response)}"
            return {"optimist": f"[Error: {error_msg}]", "optimist_error": error_msg}
        return {"optimist": response.analysis, "optimist_error": None}
    except Exception as e:
        error_msg = f"Optimist agent error: {type(e).__name__} - {str(e)}"
        return {"optimist": f"[Error: {error_msg}]", "optimist_error": error_msg}


async def pessimist_node(state: DebateState) -> Dict[str, Any]:
    """Run the Pessimist persona and store its output."""
    prompt = build_pessimist_prompt(state)
    try:
        result = instructor_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            response_model=AgentResponse,
            temperature=0.7,
        )
        # Handle both coroutine (needs await) and direct return (already resolved)
        if hasattr(result, '__await__'):
            response = await result
        else:
            response = result

        if not isinstance(response, AgentResponse):
            error_msg = f"Pessimist agent error: Expected AgentResponse, got {type(response)}"
            return {"pessimist": f"[Error: {error_msg}]", "pessimist_error": error_msg}
        return {"pessimist": response.analysis, "pessimist_error": None}
    except Exception as e:
        error_msg = f"Pessimist agent error: {type(e).__name__} - {str(e)}"
        return {"pessimist": f"[Error: {error_msg}]", "pessimist_error": error_msg}

# ---------------------------------------------------------------------------
# Synthesiser node – combines the three perspectives.
# ---------------------------------------------------------------------------

def _weighted_summary(realist: str, optimist: str, pessimist: str) -> str:
    """Create a concise final synthesis from all three agent responses."""
    parts = []
    for label, content in [("Realist", realist), ("Optimist", optimist), ("Pessimist", pessimist)]:
        stripped = content.strip()
        if stripped and not stripped.startswith("[Error"):
            # Extract just the bullet points if they exist
            lines = stripped.split("\n")
            bullets = [line for line in lines if line.strip().startswith("-")]
            if bullets:
                parts.append(f"## {label} Assessment\n" + "\n".join(bullets))
            else:
                parts.append(f"## {label} Assessment\n{stripped}")

    return "\n\n".join(parts)


async def synthesizer_node(state: DebateState) -> Dict[str, Any]:
    """Combine the three agent outputs into a final audit narrative.

    The node expects ``state["realist"]``, ``state["optimist"]`` and
    ``state["pessimist"]`` to be present.

    It also checks for agent errors and marks the debate as incomplete
    if any agent failed.
    """
    realist = state.get("realist", "")
    optimist = state.get("optimist", "")
    pessimist = state.get("pessimist", "")

    # Check for agent errors
    realist_error = state.get("realist_error")
    optimist_error = state.get("optimist_error")
    pessimist_error = state.get("pessimist_error")

    errors = []
    if realist_error:
        errors.append(f"Realist: {realist_error}")
    if optimist_error:
        errors.append(f"Optimist: {optimist_error}")
    if pessimist_error:
        errors.append(f"Pessimist: {pessimist_error}")

    synthesis = _weighted_summary(realist, optimist, pessimist)

    # If any agent failed, prepend error notice to synthesis
    if errors:
        error_notice = "[INCOMPLETE DEBATE - The following agents failed:\n" + "\n".join(errors) + "\n]\n\n"
        synthesis = error_notice + synthesis

    return {"synthesis": synthesis}

# ---------------------------------------------------------------------------
# Graph construction – compiled once for reuse.
# ---------------------------------------------------------------------------

def _build_graph() -> StateGraph:
    graph = StateGraph(DebateState)
    # Register node functions
    graph.add_node("realist", realist_node)
    graph.add_node("optimist", optimist_node)
    graph.add_node("pessimist", pessimist_node)
    graph.add_node("synthesizer", synthesizer_node)

    # Parallel fan‑out from START to the three agents
    graph.add_edge(START, "realist")
    graph.add_edge(START, "optimist")
    graph.add_edge(START, "pessimist")

    # All three converge onto the synthesiser
    graph.add_edge("realist", "synthesizer")
    graph.add_edge("optimist", "synthesizer")
    graph.add_edge("pessimist", "synthesizer")

    # End of the workflow
    graph.add_edge("synthesizer", END)
    return graph.compile()

# Compile the graph at import time – cheap because the nodes are async.
_DEBATE_GRAPH = _build_graph()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_debate(initial_state: DebateState) -> DebateState:
    """Execute the debate graph.

    ``initial_state`` should contain any upstream data (evidence, version info,
    calculations, findings). The returned dict includes the three persona
    analyses and the final ``synthesis``.
    """
    # ``invoke`` returns the final state dict.
    final_state = await _DEBATE_GRAPH.ainvoke(initial_state)
    return final_state

# ---------------------------------------------------------------------------
# Example usage (for interactive testing – not executed on import)
# ---------------------------------------------------------------------------
# if __name__ == "__main__":
#     import asyncio
#
#     async def demo():
#         init = {
#             "evidence": "Sample evidence…",
#             "version_info": "v1.2",
#             "calculations": "...",
#             "findings": "...",
#         }
#         result = await run_debate(init)
#         print(result["synthesis"])
#
#     asyncio.run(demo())
