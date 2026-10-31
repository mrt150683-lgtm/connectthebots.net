"""One authoritative release contract; no inference or network configuration."""
from __future__ import annotations

from functools import lru_cache
from fractions import Fraction
from importlib.resources import files
from copy import deepcopy
from datetime import datetime
from typing import Any

from .numeric import exact, strict_json_loads

FAMILIES = ("capability_complementarity", "goal_problem_alignment", "practical_workflow_fit", "communication_fit")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    try:
        contract = strict_json_loads(files("agentmatch").joinpath("data/release_defaults.json").read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError("INVALID_CONFIGURATION: packaged release contract unavailable") from exc
    validate_contract(contract)
    return contract


def validate_contract(contract: Any) -> None:
    """Reject malformed limits before any command or state transition runs."""
    if not isinstance(contract, dict) or contract.get("contract_version") != "0.4.0" or contract.get("initial_release") != "R0":
        raise ValueError("UNSUPPORTED_CONTRACT: expected 0.4.0 / R0")
    try:
        policy = contract["scoring_fixture_policy"]
        if not isinstance(policy, dict):
            raise ValueError("policy must be object")
        for name in ("weights", "selectivity_presets", "evidence_factors"):
            if not isinstance(policy[name], dict):
                raise ValueError("policy registry must be object")
        if (type(policy["display_precision_digits"]) is not int or not 1 <= policy["display_precision_digits"] <= 64
                or policy["display_rounding"] != "ROUND_HALF_EVEN"):
            raise ValueError("unsupported numeric display policy")
        if set(policy["weights"]) != set(FAMILIES):
            raise ValueError("invalid families")
        if sum(exact(value, low=0, high=1) for value in policy["weights"].values()) != 1:
            raise ValueError("weights must sum to one")
        if set(policy["selectivity_presets"]) != {"open", "selective", "close_fit"}:
            raise ValueError("invalid preset registry")
        if policy["default_selectivity_preset"] not in policy["selectivity_presets"]:
            raise ValueError("invalid default preset")
        for value in policy["selectivity_presets"].values():
            exact(value, low=0, high=1)
        exact(policy["minimum_coverage_each_direction"], low=0, high=1)
        exact(policy["score_tie_epsilon"], low=0, high=1)
        if set(policy["evidence_factors"]) != {"current_owner_statement", "approved_contextual_inference", "unapproved_or_disallowed_or_invalid"}:
            raise ValueError("invalid evidence factor registry")
        for value in policy["evidence_factors"].values():
            exact(value, low=0, high=1)
        if exact(policy["evidence_factors"]["unapproved_or_disallowed_or_invalid"]) != 0:
            raise ValueError("invalid evidence cannot carry weight")
        for section in ("r0", "protocol_pilot", "interview", "social", "offline", "transport", "local_inference", "infrastructure"):
            if not isinstance(contract[section], dict):
                raise ValueError("section must be object")
        limits = {
            "r0": ("synthetic_owner_count", "target_conversation_count", "conversations_per_owner_default", "conversations_per_owner_min", "conversations_per_owner_max", "minimum_named_edge_scenarios", "synthetic_offer_limit_per_run"),
            "protocol_pilot": ("max_candidates_shortlisted_per_run", "max_peers_probed_per_run", "max_clarification_rounds_per_pair", "max_questions_total_per_pair", "max_encoded_message_bytes", "evaluation_session_ttl_seconds", "proposal_ttl_seconds", "max_active_sessions_per_peer_and_purpose", "max_active_sessions_per_owner", "max_outbound_questions_per_owner_per_utc_day", "max_inbound_questions_answered_per_owner_per_utc_day", "max_new_sessions_per_owner_per_utc_day", "max_contact_releases_per_owner_per_utc_day"),
            "interview": ("default_max_questions_including_followups", "max_followups_per_dimension_per_session", "extended_max_questions_total", "review_max_claims_per_batch", "review_generated_tokens_max"),
            "social": ("max_suggested_topic_threads", "max_encoded_chat_message_bytes"),
            "offline": ("max_queued_messages_per_channel", "default_unsent_expiry_seconds"),
            "transport": ("signaling_max_encoded_bytes", "signaling_envelope_ttl_seconds"),
            "local_inference": ("max_concurrent_jobs", "max_loaded_models", "context_tokens_max"),
        }
        for section, keys in limits.items():
            for key in keys:
                value = contract[section][key]
                if type(value) is not int or not 1 <= value <= 31536000:
                    raise ValueError("bounded positive integer limit required")
        for section, key, maximum in (("r0", "default_seed", 2**32 - 1),
                                      ("protocol_pilot", "clock_skew_tolerance_seconds", 86400),
                                      ("infrastructure", "r0_r1_public_servers_required", 0)):
            value = contract[section][key]
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("bounded nonnegative integer required")
        required_switches = {
            "r0": {"requires_network_after_install": False, "requires_api_key": False, "requires_gpu": False, "requires_model_weights": False, "allow_real_history_import": False, "allow_contact_transmission": False, "synthetic_only_interviews": True},
            "protocol_pilot": {"requires_authenticated_peer_binding": True, "requires_verified_peer_invitation": True, "requires_both_peers_online": True, "allow_early_application_messages": False},
            "interview": {"sensitive_dimensions_enabled_by_default": False, "auto_approve_extracted_claims": False, "allow_interview_only_onboarding": True, "extension_requires_explicit_owner_choice": True, "prior_questions_count_toward_extended_total": True, "refusal_can_create_trait": False, "allow_model_generated_confidence_probability": False, "summary_acknowledgment_approves_claims": False, "review_can_bypass_question_budget": False, "private_input_requires_verified_local_controller": True, "review_prefer_deterministic_claim_rendering": True},
            "social": {"chat_content_available_to_coach_by_default": False, "shared_agent_turns_enabled_by_default": False, "require_exact_shared_context_acceptance": True},
            "offline": {"requires_online_peers_for_delivery": True, "retry_same_message_id": True, "remote_queue_enabled": False},
            "transport": {"rendezvous_stores_user_messages": False, "durable_mailbox_enabled": False, "source_vectors_on_wire": False},
            "local_inference": {"remote_or_cloud_inference_allowed": False, "manual_mode_available": True, "model_install_requires_explicit_choice": True, "private_worker_can_download": False, "private_worker_external_network_allowed": False},
            "infrastructure": {"inference_service_allowed": False, "central_private_profile_or_vector_store_allowed": False, "application_payload_persistence_allowed": False, "request_body_logging_allowed": False, "turn_requires_owner_connection_policy": True, "deployment_authorised": False},
        }
        for section, entries in required_switches.items():
            for key, required in entries.items():
                if contract[section][key] is not required:
                    raise ValueError("required safety switch changed")
        r0, protocol, interview = contract["r0"], contract["protocol_pilot"], contract["interview"]
        if contract["default_mode"] != "collaboration" or not isinstance(contract["plan_version"], str):
            raise ValueError("invalid R0 mode or plan version")
        if not isinstance(r0["default_as_of"], str) or len(r0["default_as_of"]) > 40:
            raise ValueError("invalid frozen clock")
        frozen_clock = datetime.fromisoformat(r0["default_as_of"].replace("Z", "+00:00"))
        if frozen_clock.tzinfo is None or frozen_clock.utcoffset() is None:
            raise ValueError("frozen clock needs timezone")
        if not r0["conversations_per_owner_min"] <= r0["conversations_per_owner_default"] <= r0["conversations_per_owner_max"]:
            raise ValueError("inconsistent conversation bounds")
        if not r0["synthetic_owner_count"] * r0["conversations_per_owner_min"] <= r0["target_conversation_count"] <= r0["synthetic_owner_count"] * r0["conversations_per_owner_max"]:
            raise ValueError("unattainable corpus count")
        if r0["synthetic_offer_limit_per_run"] > protocol["max_candidates_shortlisted_per_run"] or protocol["max_peers_probed_per_run"] > protocol["max_candidates_shortlisted_per_run"]:
            raise ValueError("run budgets exceed candidate shortlist")
        if not interview["max_followups_per_dimension_per_session"] <= interview["default_max_questions_including_followups"] <= interview["extended_max_questions_total"] <= 256:
            raise ValueError("inconsistent interview budgets")
        if interview["review_max_claims_per_batch"] > 256 or contract["social"]["max_suggested_topic_threads"] > 16:
            raise ValueError("unbounded review or topic display")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("INVALID_CONFIGURATION: contract violates R0 policy") from exc


def load_defaults() -> dict[str, Any]:
    """Return a defensive copy: callers cannot mutate the process-wide policy."""
    return deepcopy(_load())


def selectivity(preset: str | None = None, custom: str | None = None) -> Fraction:
    policy = _load()["scoring_fixture_policy"]
    if preset is not None and custom is not None:
        raise ValueError("CONFLICTING_SELECTIVITY: choose a preset or custom threshold")
    if custom is not None:
        return exact(custom, low=0, high=1)
    selected = preset or policy["default_selectivity_preset"]
    if selected not in policy["selectivity_presets"]:
        raise ValueError("UNKNOWN_SELECTIVITY: unknown preset")
    return exact(policy["selectivity_presets"][selected], low=0, high=1)
