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
the what-if ``scenario`` and its ``calculations``. The function returns the
final state, where ``synthesis`` holds the combined narrative.
'''

from __future__ import annotations

import ast
from decimal import Decimal
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

    # Input: the what-if scenario and the partner's real figures behind it.
    scenario: str | None
    calculations: str | None

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

# Shared wording for every persona prompt (3 bullets, 60 words).
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


def build_optimist_prompt(state: DebateState) -> str:
    """Return a prompt for the Optimist persona - concise positive findings."""
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


def build_pessimist_prompt(state: DebateState) -> str:
    """Return a prompt for the Pessimist persona - concise risk findings."""
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
# One-sentence scenario summary - "This scenario would decrease your earnings
# by about Rs X because ..."
# ---------------------------------------------------------------------------
# The LLM only turns the typed scenario into a direction, a short reason and an
# arithmetic formula over the partner's named figures. The rupee amount is
# computed here by evaluating that formula - never written by the LLM.

class ScenarioImpact(BaseModel):
    direction: Literal["increase", "decrease", "no_change", "unclear"] = Field(
        ..., description="Effect of the scenario on the partner's earnings"
    )
    change_formula: str | None = Field(
        default=None,
        description=(
            "Arithmetic for the change in rupees using only the listed figure names and "
            "numbers stated in the scenario, e.g. 'platform_peak_hour_incentives * 0.20'. "
            "Positive means more money, negative means less. Null if it cannot be worked out."
        ),
    )
    reason: str = Field(..., description="One short simple-English clause saying why, without the word 'because'")


_ALLOWED_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}
_OP_SYMBOLS = {ast.Add: "+", ast.Sub: "−", ast.Mult: "×", ast.Div: "÷"}


def evaluate_formula(formula: str, figures: Dict[str, Dict[str, Any]]) -> Decimal:
    """Evaluate +, -, *, / over named figures and plain numbers. Anything else is rejected."""
    def _eval(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = _eval(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name) and node.id in figures:
            return Decimal(str(figures[node.id]["value"]))
        raise ValueError(f"Not allowed in a scenario formula: {ast.dump(node)[:60]}")

    return _eval(ast.parse(formula, mode="eval"))


def _money_text(value: Decimal) -> str:
    text = f"{value.quantize(Decimal('0.01')):,.2f}"
    return f"₹{text[:-3] if text.endswith('.00') else text}"


def describe_formula(formula: str, figures: Dict[str, Dict[str, Any]]) -> str:
    """The formula with each figure's meaning and value, for the partner to check."""
    def _show(node: ast.AST) -> str:
        if isinstance(node, ast.Expression):
            return _show(node.body)
        if isinstance(node, ast.BinOp):
            return f"({_show(node.left)} {_OP_SYMBOLS[type(node.op)]} {_show(node.right)})"
        if isinstance(node, ast.UnaryOp):
            return f"−{_show(node.operand)}" if isinstance(node.op, ast.USub) else _show(node.operand)
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.Name):
            f = figures[node.id]
            value = _money_text(Decimal(str(f["value"]))) if f.get("money") else str(f["value"])
            return f"{f['label']} {value}"
        return "?"

    text = _show(ast.parse(formula, mode="eval"))
    return text[1:-1] if text.startswith("(") and text.endswith(")") else text


def scenario_summary_sentence(impact: Dict[str, Any], period_days: int | None) -> str:
    """The ultra-simple summary shown first. Pure formatting, no LLM."""
    reason = (impact.get("reason") or "").strip().rstrip(".")
    if reason.lower().startswith("because "):
        reason = reason[8:]
    because = f" because {reason}" if reason else ""
    over = f" over {period_days} days" if period_days else ""
    amount = impact.get("amount")
    direction = impact.get("direction")
    if amount is not None and direction in ("increase", "decrease"):
        return f"This scenario would {direction} your earnings by about {_money_text(amount)}{over}{because}."
    if direction == "no_change":
        return f"This scenario would not change your earnings{because}."
    if direction in ("increase", "decrease"):
        return (f"This scenario would likely {direction} your earnings{because}. "
                "Your data is not enough to put a rupee amount on it.")
    return f"It is not clear whether this scenario would raise or lower your earnings{because}."


async def summarize_scenario(
    scenario: str,
    figures: Dict[str, Dict[str, Any]],
    figures_text: str,
    period_days: int | None,
    personas: Dict[str, str],
) -> Dict[str, Any]:
    """Direction, amount and one simple sentence for a typed scenario."""
    prompt = (
        "A delivery partner typed a what-if scenario. Work out how it changes their earnings.\n\n"
        f"Scenario: {scenario}\n\n"
        "The partner's real figures (name: meaning = value):\n"
        f"{figures_text}\n\n"
        "Three advisers already commented (for context only):\n"
        + "\n".join(f"{k.title()}: {v}" for k, v in personas.items() if v)
        + "\n\nReturn:\n"
        "- direction: increase, decrease, no_change or unclear - the effect on the partner's earnings.\n"
        "- change_formula: arithmetic for the change in rupees using ONLY the figure names above and "
        "numbers stated in the scenario, with + - * / and brackets. Positive = more money, negative = "
        "less. Null if these figures cannot measure the change. Never invent numbers.\n"
        "- reason: one short clause in very simple English (no jargon), saying what changes, e.g. "
        "'your peak-hour bonus would be 20% higher'. Do not start with 'because'."
    )
    impact: Dict[str, Any] = {"direction": "unclear", "reason": "", "amount": None, "calculation": None}
    try:
        result = instructor_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            response_model=ScenarioImpact,
            temperature=0.0,
        )
        response = await result if hasattr(result, "__await__") else result
        impact.update(direction=response.direction, reason=response.reason)
        if response.change_formula:
            try:
                change = evaluate_formula(response.change_formula, figures)
                impact["calculation"] = f"{describe_formula(response.change_formula, figures)} = {_money_text(abs(change))}"
                impact["amount"] = abs(change).quantize(Decimal("0.01"))
                # The computed sign decides the direction, not the LLM's word.
                if change > Decimal("0.50"):
                    impact["direction"] = "increase"
                elif change < Decimal("-0.50"):
                    impact["direction"] = "decrease"
                else:
                    impact["direction"], impact["amount"] = "no_change", None
            except (ValueError, SyntaxError, ArithmeticError, KeyError):
                impact["calculation"] = None
    except Exception as exc:
        impact["error"] = f"{type(exc).__name__}: {exc}"
        impact["summary"] = (
            "FairPay could not summarise this scenario right now. See the detailed analysis below."
        )
        return impact
    impact["summary"] = scenario_summary_sentence(impact, period_days)
    return impact


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

    ``initial_state`` should contain the what-if ``scenario`` and its
    ``calculations``. The returned dict includes the three persona
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
#             "scenario": "Work 2 more hours a day",
#             "calculations": "...",
#         }
#         result = await run_debate(init)
#         print(result["synthesis"])
#
#     asyncio.run(demo())
