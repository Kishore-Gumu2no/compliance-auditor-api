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

    # Agent outputs
    realist: str | None
    optimist: str | None
    pessimist: str | None

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

instructor_client = instructor.from_groq(groq_client)

# ---------------------------------------------------------------------------
# Prompt builders – the user already wrote these in the previous session.
# We keep the signatures and import them if they exist; otherwise we provide a
# very small fallback implementation to keep the file runnable.
# ---------------------------------------------------------------------------

def build_realist_prompt(state: DebateState) -> str:
    """Return a prompt for the Realist persona.

    The prompt should contain the evidence, version info, calculations and any
    findings that are already available in ``state``.
    """
    # Simple fallback – in the real code the user supplies a richer prompt.
    base = "You are the REALIST. Provide a pragmatic analysis of the compliance audit."
    details = []
    for key in ("evidence", "version_info", "calculations", "findings"):
        if value := state.get(key):
            details.append(f"{key.replace('_', ' ').title()}: {value}\n")
    return base + "\n\n" + "".join(details)


def build_optimist_prompt(state: DebateState) -> str:
    """Return a prompt for the Optimist persona."""
    base = "You are the OPTIMIST. Highlight the most favorable compliance outcomes."
    details = []
    for key in ("evidence", "version_info", "calculations", "findings"):
        if value := state.get(key):
            details.append(f"{key.replace('_', ' ').title()}: {value}\n")
    return base + "\n\n" + "".join(details)


def build_pessimist_prompt(state: DebateState) -> str:
    """Return a prompt for the PESSIMIST persona."""
    base = "You are the PESSIMIST. Emphasise the risks and worst‑case compliance failures."
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
    response: AgentResponse = await instructor_client.chat.completions.create(
        model="groq‑mixtral‑8x7b‑32768",
        messages=[{"role": "user", "content": prompt}],
        response_model=AgentResponse,
        temperature=0.3,
    )
    return {"realist": response.analysis}


async def optimist_node(state: DebateState) -> Dict[str, Any]:
    """Run the Optimist persona and store its output."""
    prompt = build_optimist_prompt(state)
    response: AgentResponse = await instructor_client.chat.completions.create(
        model="groq‑mixtral‑8x7b‑32768",
        messages=[{"role": "user", "content": prompt}],
        response_model=AgentResponse,
        temperature=0.6,
    )
    return {"optimist": response.analysis}


async def pessimist_node(state: DebateState) -> Dict[str, Any]:
    """Run the Pessimist persona and store its output."""
    prompt = build_pessimist_prompt(state)
    response: AgentResponse = await instructor_client.chat.completions.create(
        model="groq‑mixtral‑8x7b‑32768",
        messages=[{"role": "user", "content": prompt}],
        response_model=AgentResponse,
        temperature=0.7,
    )
    return {"pessimist": response.analysis}

# ---------------------------------------------------------------------------
# Synthesiser node – combines the three perspectives.
# ---------------------------------------------------------------------------

def _weighted_summary(realist: str, optimist: str, pessimist: str) -> str:
    """Create a short synthesis.

    The simple heuristic gives equal weight but prefixes each paragraph with the
    persona name.
    """
    parts = [
        f"**Realist**: {realist.strip()}",
        f"**Optimist**: {optimist.strip()}",
        f"**Pessimist**: {pessimist.strip()}",
    ]
    return "\n\n".join(parts)


async def synthesizer_node(state: DebateState) -> Dict[str, Any]:
    """Combine the three agent outputs into a final audit narrative.

    The node expects ``state[\"realist\"]``, ``state[\"optimist\"]`` and
    ``state[\"pessimist\"]`` to be present.
    """
    realist = state.get("realist", "")
    optimist = state.get("optimist", "")
    pessimist = state.get("pessimist", "")
    synthesis = _weighted_summary(realist, optimist, pessimist)
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
