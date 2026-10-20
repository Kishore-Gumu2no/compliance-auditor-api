"""The same evidence must always produce the same verdict.

A pay audit that answers differently on two runs of identical evidence is
not evidence of anything: the partner cannot act on it, and neither can a
platform asked to correct a shortfall. This file pins that property down by
running one partner's evidence through the audit pipeline 50 times and
requiring the serialised verdict to be byte-identical every time.

The run uses the synthetic dataset and the local law fixture, so nothing
here depends on Qdrant, Groq or Postgres being reachable -- which is the
point: the determinism being checked is the engine's own, not a service's.

    python -m pytest test_audit_determinism.py -q
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from synthetic_verification import (  # noqa: E402
    get_default_contract_versions,
    load_synthetic_data,
    run_engine_on_partner,
)

DATA_ROOT = BACKEND.parent / "synthetic_data"

# High enough that a 1-in-a-few-runs ordering flip shows up, low enough to
# stay inside a normal test run.
RUNS = 50

# PTR0015 carries seeded anomalies across all three Layer 2 categories and
# the largest transaction count in the set, so it exercises the widest slice
# of the pipeline available from a single partner.
PARTNER_ID = "PTR0015"


def _verdict_bytes(state, decisions, summary) -> bytes:
    """Serialise everything the audit actually decides, canonically.

    Keys are sorted and every value is rendered through ``str`` so Decimals,
    dates and enums compare by their printed form -- the form a partner is
    shown. ``default=str`` is deliberate rather than lenient: a verdict that
    reads the same but is a different object is still the same verdict.
    """
    verdict = {
        "status": state.get("status"),
        "error": state.get("error"),
        "payment_calculations": state.get("payment_calculations"),
        "business_cost_calculations": state.get("business_cost_calculations"),
        "audit_findings": state.get("audit_findings"),
        "applicable_laws": state.get("applicable_laws"),
        "evidence_gaps": state.get("evidence_gaps"),
        "classification_probability": state.get("classification_probability"),
        "controller_decisions": decisions,
        "batch_summary": summary,
    }
    return json.dumps(verdict, sort_keys=True, default=str).encode("utf-8")


@pytest.fixture(scope="module")
def dataset():
    if not DATA_ROOT.exists():
        pytest.skip(f"synthetic data not found at {DATA_ROOT}")
    return load_synthetic_data(str(DATA_ROOT))


def test_fifty_runs_of_one_evidence_set_give_one_verdict(dataset, capsys):
    contracts = get_default_contract_versions()

    digests: list[str] = []
    first: bytes | None = None
    for run in range(RUNS):
        with _quiet(capsys):
            state, decisions, summary = run_engine_on_partner(
                PARTNER_ID, dataset, contracts
            )
        payload = _verdict_bytes(state, decisions, summary)
        if first is None:
            first = payload
        digests.append(hashlib.sha256(payload).hexdigest())

    assert state.get("status") == "complete", (
        f"the pipeline did not complete: {state.get('error')!r}"
    )

    distinct = sorted(set(digests))
    assert len(distinct) == 1, (
        f"{len(distinct)} different verdicts across {RUNS} runs of identical "
        f"evidence for {PARTNER_ID}: {distinct}"
    )

    # Reported so the figure is readable in the run log, not just asserted.
    print(
        f"\n{RUNS} runs of {PARTNER_ID}: 1 distinct verdict, "
        f"sha256={distinct[0]}, {len(first)} bytes"
    )


class _quiet:
    """Swallow the pipeline's per-node debug prints for the 50 repeats."""

    def __init__(self, capsys):
        self._capsys = capsys

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._capsys.readouterr()
        return False
