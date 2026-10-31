"""Deterministic local explanations grounded in the actual assessed atoms."""
from __future__ import annotations

from typing import Any
from .models import DirectionalAssessment, PairAssessment


def render_local(assessment: DirectionalAssessment) -> dict[str, Any]:
    """Private evidence explanation, never an outbound proposal serializer."""
    positives = [atom.to_dict() for atom in assessment.atoms if atom.known and atom.fit > 0]
    negatives = [atom.to_dict() for atom in assessment.atoms if atom.known and atom.fit == 0]
    unknowns = [atom.to_dict() for atom in assessment.atoms if not atom.known]
    if assessment.threshold == 0 and assessment.qualifies:
        summary = "Passes the selected policy with no soft-preference filter; this does not establish compatibility."
    elif assessment.qualifies:
        summary = "Passes this owner's current collaboration preferences and evidence gates."
    elif assessment.status == "insufficient_evidence":
        summary = "Available approved evidence does not settle this owner's policy."
    elif assessment.status == "not_recommended":
        summary = "The current known and still-possible fit cannot reach this owner's threshold."
    elif assessment.status == "not_eligible":
        summary = "A current hard constraint or capacity rule prevents this introduction."
    else:
        summary = "A current intent or required fact is unavailable."
    return {"owner_id": assessment.seeker_id, "candidate_id": assessment.candidate_id, "summary": summary,
            "status": assessment.status, "reasons": list(assessment.reasons), "supported_atoms": positives,
            "known_mismatches": negatives, "unknown_atoms": unknowns,
            "visibility": "local_private_card", "warning": "This private explanation includes local claim references and must never be sent as a peer card."}


def render_pair_local(pair: PairAssessment) -> dict[str, Any]:
    return {"qualified_pair": pair.qualifies, "a_to_b": render_local(pair.a_to_b), "b_to_a": render_local(pair.b_to_a),
            "mode": "offline_synthetic_reference_only"}
