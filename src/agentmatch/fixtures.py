"""Bounded loading of the bundled, wholly fictional R0 reference corpus.

There is deliberately no path, URL, account, or real-history input here.  The
annotations are authored fixtures; no natural-language extraction is claimed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from importlib import resources
import json
import re
from typing import Any, Mapping

from .config import load_defaults
from .models import Owner, load_owner, parse_time


_CORPUS_ID = "agentmatch-r0-fiction-1"
_PROVENANCE = "wholly_authored_fiction_no_real_people_or_archives"
_FILES = frozenset({"corpus.json", "expected_outcomes.json"})
_ID = re.compile(r"[a-z][a-z0-9-]*(?::[a-z0-9-]+)?\Z")
_MAX_FILE_BYTES = 2_000_000


class FixtureError(ValueError):
    """Stable, payload-free error raised before evaluation begins."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FixtureCorpus:
    owners: tuple[Owner, ...]
    conversations: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]
    expected_outcomes: tuple[dict[str, Any], ...]

    def owner(self, owner_id: str) -> Owner:
        for item in self.owners:
            if item.owner_id == owner_id:
                return item
        raise FixtureError("UNKNOWN_SYNTHETIC_OWNER")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise FixtureError("NONFINITE_FIXTURE_NUMBER")


def _decode(payload: bytes) -> dict[str, Any]:
    if len(payload) > _MAX_FILE_BYTES:
        raise FixtureError("FIXTURE_FILE_TOO_LARGE")
    try:
        parsed = json.loads(
            payload, object_pairs_hook=_object, parse_float=Decimal,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise FixtureError("INVALID_FIXTURE_JSON") from exc
    if not isinstance(parsed, dict):
        raise FixtureError("INVALID_FIXTURE_OBJECT")
    return parsed


def _keys(obj: Any, names: set[str], code: str) -> None:
    if not isinstance(obj, dict) or set(obj) != names:
        raise FixtureError(code)


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 160 and bool(_ID.fullmatch(value))


def _positive_int(value: Any) -> bool:
    return type(value) is int and 0 < value < 10_000


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    _keys(manifest, {
        "schema_version", "fixture_version", "corpus_id", "provenance",
        "owner_count", "conversation_count", "conversations_per_owner",
        "source_message_count", "minimum_edge_scenarios", "named_case_count",
        "files", "default_owner", "default_intent", "default_as_of",
        "claims_are", "availability_notice", "reference_corpus_used",
        "real_owner_data_used",
    }, "INVALID_FIXTURE_MANIFEST")
    if (manifest["schema_version"] != "0.1" or manifest["fixture_version"] != "1"
            or manifest["corpus_id"] != _CORPUS_ID
            or manifest["provenance"] != _PROVENANCE
            or manifest["reference_corpus_used"] is not False
            or manifest["real_owner_data_used"] is not False):
        raise FixtureError("UNSUPPORTED_FIXTURE_PROVENANCE_OR_VERSION")
    files = manifest["files"]
    if not isinstance(files, dict) or set(files) != _FILES:
        raise FixtureError("UNSAFE_FIXTURE_FILE_REFERENCE")
    for descriptor in files.values():
        _keys(descriptor, {"sha256", "bytes"}, "INVALID_FIXTURE_FILE_DESCRIPTOR")
        if (not isinstance(descriptor["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"]) is None
                or type(descriptor["bytes"]) is not int
                or not 0 < descriptor["bytes"] <= _MAX_FILE_BYTES):
            raise FixtureError("INVALID_FIXTURE_FILE_DESCRIPTOR")
    for key in ("owner_count", "conversation_count", "conversations_per_owner",
                "source_message_count", "minimum_edge_scenarios", "named_case_count"):
        if not _positive_int(manifest[key]):
            raise FixtureError("INVALID_FIXTURE_MANIFEST_COUNT")


def _validate_corpus(
    corpus: Mapping[str, Any], manifest: Mapping[str, Any]
) -> tuple[tuple[Owner, ...], tuple[dict[str, Any], ...]]:
    """Validate structural/provenance claims, never infer facts from dialogue."""
    _keys(corpus, {"schema_version", "corpus_id", "provenance", "owners", "conversations"},
          "INVALID_CORPUS_SCHEMA")
    if (corpus["schema_version"] != "0.1" or corpus["corpus_id"] != _CORPUS_ID
            or corpus["provenance"] != _PROVENANCE):
        raise FixtureError("UNSUPPORTED_CORPUS_PROVENANCE_OR_VERSION")
    settings = load_defaults()["r0"]
    raw_owners = corpus["owners"]
    conversations = corpus["conversations"]
    if (not isinstance(raw_owners, list)
            or len(raw_owners) != settings["synthetic_owner_count"]
            or len(raw_owners) != manifest["owner_count"]
            or not isinstance(conversations, list)
            or len(conversations) != settings["target_conversation_count"]
            or len(conversations) != manifest["conversation_count"]):
        raise FixtureError("FIXTURE_COUNT_MISMATCH")
    owner_ids: set[str] = set()
    for raw in raw_owners:
        if (not isinstance(raw, dict) or not _identifier(raw.get("owner_id"))
                or raw.get("synthetic") is not True):
            raise FixtureError("INVALID_SYNTHETIC_OWNER")
        if raw["owner_id"] in owner_ids:
            raise FixtureError("DUPLICATE_OWNER_ID")
        owner_ids.add(raw["owner_id"])

    conversation_ids: set[str] = set()
    source_records: dict[str, tuple[str, str, bool]] = {}
    counts = dict.fromkeys(owner_ids, 0)
    for conversation in conversations:
        _keys(conversation, {"conversation_id", "owner_id", "synthetic", "authored_at",
                             "source_kind", "messages"}, "INVALID_CONVERSATION_SCHEMA")
        cid = conversation["conversation_id"]
        oid = conversation["owner_id"]
        if (not _identifier(cid) or cid in conversation_ids
                or oid not in owner_ids or conversation["synthetic"] is not True
                or conversation["source_kind"] != "authored_fiction"):
            raise FixtureError("INVALID_OR_DUPLICATE_CONVERSATION")
        try:
            parse_time(conversation["authored_at"])
        except (TypeError, ValueError) as exc:
            raise FixtureError("INVALID_SOURCE_TIME") from exc
        conversation_ids.add(cid)
        counts[oid] += 1
        messages = conversation["messages"]
        if not isinstance(messages, list) or not 2 <= len(messages) <= 12:
            raise FixtureError("INVALID_MESSAGE_COUNT")
        for message in messages:
            _keys(message, {"message_id", "role", "author_id", "text", "quoted",
                            "origin_event_id"}, "INVALID_SOURCE_SCHEMA")
            sid = message["message_id"]
            role = message["role"]
            if (not _identifier(sid) or sid in source_records
                    or not sid.startswith(cid + ":")
                    or role not in {"agent", "owner"}
                    or type(message["quoted"]) is not bool
                    or not _identifier(message["origin_event_id"])
                    or not message["origin_event_id"].startswith(cid + ":")
                    or not isinstance(message["text"], str)
                    or not 1 <= len(message["text"]) <= 8192):
                raise FixtureError("INVALID_OR_DUPLICATE_SOURCE")
            if ((role == "owner" and message["author_id"] != oid)
                    or (role == "agent" and message["author_id"] != "fictional-interviewer")):
                raise FixtureError("SOURCE_AUTHOR_MISMATCH")
            try:
                message["text"].encode("utf-8", "strict")
            except UnicodeError as exc:
                raise FixtureError("INVALID_SOURCE_UNICODE") from exc
            source_records[sid] = (oid, role, message["quoted"])
    if (any(not settings["conversations_per_owner_min"] <= count
            <= settings["conversations_per_owner_max"] for count in counts.values())
            or any(count != manifest["conversations_per_owner"] for count in counts.values())
            or len(source_records) != manifest["source_message_count"]):
        raise FixtureError("SOURCE_COUNT_MISMATCH")

    owners: list[Owner] = []
    all_claim_versions: set[tuple[str, int]] = set()
    all_intents: set[str] = set()
    for raw in raw_owners:
        oid = raw["owner_id"]
        try:
            typed = load_owner(raw)
        except (ValueError, TypeError, KeyError) as exc:
            raise FixtureError("INVALID_TYPED_OWNER") from exc
        if typed.intent.intent_id in all_intents:
            raise FixtureError("DUPLICATE_INTENT_ID")
        all_intents.add(typed.intent.intent_id)
        for claim in raw["claims"]:
            version_key = (claim["claim_id"], claim["version"])
            if version_key in all_claim_versions:
                raise FixtureError("DUPLICATE_CLAIM_VERSION")
            all_claim_versions.add(version_key)
            if not claim["source_refs"]:
                raise FixtureError("MISSING_SOURCE_REFERENCE")
            for source_id in claim["source_refs"]:
                source = source_records.get(source_id)
                if source is None or source[0] != oid:
                    raise FixtureError("UNSAFE_OR_MISSING_SOURCE_REFERENCE")
                if source[1] != "owner" or source[2]:
                    raise FixtureError("NON_OWNER_EVIDENCE_REFERENCE")
            for reference in claim["contradiction_refs"]:
                if reference not in source_records or source_records[reference][0] != oid:
                    raise FixtureError("INVALID_CONTRADICTION_REFERENCE")
        for field in ("blocked_owner_ids", "existing_offer_owner_ids"):
            if any(peer not in owner_ids or peer == oid for peer in raw.get(field, [])):
                raise FixtureError("UNKNOWN_PEER_REFERENCE")
        owners.append(typed)
    if manifest["default_owner"] not in owner_ids or manifest["default_intent"] not in all_intents:
        raise FixtureError("UNKNOWN_DEFAULT_OWNER_OR_INTENT")
    return tuple(owners), tuple(conversations)


def _validate_cases(
    expected: Mapping[str, Any], manifest: Mapping[str, Any], owner_ids: set[str]
) -> tuple[dict[str, Any], ...]:
    _keys(expected, {"schema_version", "oracle_version", "cases", "notice"},
          "INVALID_ORACLE_SCHEMA")
    if expected["schema_version"] != "0.1" or expected["oracle_version"] != "1":
        raise FixtureError("UNSUPPORTED_ORACLE_VERSION")
    cases = expected["cases"]
    if (not isinstance(cases, list) or len(cases) != manifest["named_case_count"]
            or len(cases) < load_defaults()["r0"]["minimum_named_edge_scenarios"]):
        raise FixtureError("ORACLE_COUNT_MISMATCH")
    ids: set[str] = set()
    scenarios: set[str] = set()
    for case in cases:
        _keys(case, {"test_id", "scenario_id", "seeker_id", "candidate_id", "kind",
                     "expected", "reasoning", "authorship"}, "INVALID_ORACLE_CASE")
        if (not isinstance(case["test_id"], str)
                or re.fullmatch(r"T-[A-Z]+-[0-9]{3}", case["test_id"]) is None
                or not isinstance(case["scenario_id"], str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", case["scenario_id"]) is None
                or case["test_id"] in ids or case["scenario_id"] in scenarios
                or case["seeker_id"] not in owner_ids or case["candidate_id"] not in owner_ids
                or case["kind"] not in {"base_corpus", "independent_scenario_oracle"}
                or not isinstance(case["reasoning"], str) or not case["reasoning"]
                or not isinstance(case["expected"], dict)
                or case["authorship"] != "hand_authored_from_contract_not_scorer_output"):
            raise FixtureError("INVALID_OR_DUPLICATE_ORACLE_CASE")
        ids.add(case["test_id"])
        scenarios.add(case["scenario_id"])
    return tuple(cases)


def load_corpus() -> FixtureCorpus:
    """Load only the packaged synthetic corpus after hash and schema checks."""
    directory = resources.files("agentmatch").joinpath("data", "synthetic")
    try:
        manifest = _decode(directory.joinpath("manifest.json").read_bytes())
        _validate_manifest(manifest)
        objects: dict[str, dict[str, Any]] = {}
        for name in sorted(_FILES):
            payload = directory.joinpath(name).read_bytes()
            descriptor = manifest["files"][name]
            if (len(payload) != descriptor["bytes"]
                    or sha256(payload).hexdigest() != descriptor["sha256"]):
                raise FixtureError("FIXTURE_INTEGRITY_MISMATCH")
            objects[name] = _decode(payload)
    except (FileNotFoundError, OSError) as exc:
        raise FixtureError("BUNDLED_FIXTURE_UNAVAILABLE") from exc
    owners, conversations = _validate_corpus(objects["corpus.json"], manifest)
    cases = _validate_cases(objects["expected_outcomes.json"], manifest,
                            {owner.owner_id for owner in owners})
    return FixtureCorpus(owners, conversations, manifest, cases)


def list_owners() -> tuple[dict[str, str], ...]:
    return tuple({"owner_id": owner.owner_id, "alias": owner.alias,
                  "intent_id": owner.intent.intent_id} for owner in load_corpus().owners)


def verify() -> dict[str, Any]:
    """Return integrity/provenance evidence, not a matcher efficacy result."""
    corpus = load_corpus()
    return {
        "status": "passed", "release": "R0", "fixture_version": corpus.manifest["fixture_version"],
        "corpus_id": corpus.manifest["corpus_id"], "owner_count": len(corpus.owners),
        "conversation_count": len(corpus.conversations),
        "source_message_count": sum(len(item["messages"]) for item in corpus.conversations),
        "named_oracle_count": len(corpus.expected_outcomes),
        "named_oracle_ids": [item["test_id"] for item in corpus.expected_outcomes],
        "files": corpus.manifest["files"],
        "checks": ["bundled_file_hashes", "exact_counts", "unique_ids", "strict_typed_owners",
                   "source_author_and_reference_integrity", "case_reference_integrity", "fictional_provenance"],
        "annotation_mode": "manual_fixture_annotations",
        "numeric_oracles_executed_by_this_command": False,
        "real_owner_data_used": False, "reference_corpus_used": False,
        "limitation": "Authored fixtures test software behavior; they are not available people, model extraction results, or evidence of human compatibility.",
    }


def _fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _numbers(assessment: Any) -> dict[str, Any]:
    return {"F": _fraction_text(assessment.preference_fit),
            "S": _fraction_text(assessment.supported_fit),
            "C": _fraction_text(assessment.coverage),
            "U": _fraction_text(assessment.preference_fit_upper_bound),
            "status": assessment.status}


def _changed_claim(owner: Owner, field: str, **changes: Any) -> Owner:
    raw = owner.to_dict()
    matches = [claim for claim in raw["claims"] if claim["dimension"] == field]
    if len(matches) != 1:
        raise FixtureError("ORACLE_DIMENSION_NOT_UNIQUE")
    matches[0].update(changes)
    return load_owner(raw)


def _component_fixture(
    seeker: Owner, candidate: Owner, capability: list[bool | None],
    goal: list[bool | None], workflow: list[bool | None], communication: list[bool | None],
) -> tuple[Owner, Owner]:
    """Construct declared known/unknown atom inputs, not computed expectations."""
    from copy import deepcopy
    left, right = seeker.to_dict(), candidate.to_dict()
    left["intent"]["hard_constraints"] = []
    left["blocked_owner_ids"] = []
    right["blocked_owner_ids"] = []
    left["existing_offer_owner_ids"] = []
    right["existing_offer_owner_ids"] = []
    # Remove old soft claims. A separate fresh annotation backs each test atom.
    template = deepcopy(right["claims"][0])
    right["claims"] = []
    left["intent"]["atoms"] = {}
    patterns = (
        ("capability_complementarity", "offer.capabilities", capability),
        ("goal_problem_alignment", "intent.problem_tags", goal),
        ("practical_workflow_fit", "workflow.oracle", workflow),
        ("communication_fit", "communication.oracle", communication),
    )
    for family, field, states in patterns:
        atoms: list[dict[str, Any]] = []
        if family in {"capability_complementarity", "goal_problem_alignment"}:
            if None in states and False in states:
                raise FixtureError("ORACLE_SET_PATTERN_UNREPRESENTABLE")
            tokens = [f"oracle-{family}-{i}" for i in range(len(states))]
            atoms = [{"atom_id":f"oracle-{family}-{i}", "field":field,
                      "kind":"membership", "wanted":token} for i,token in enumerate(tokens)]
            record = deepcopy(template)
            record.update(claim_id=f"oracle-{family}", dimension=field,
                          value=[token for token,state in zip(tokens, states) if state is True],
                          complete=None not in states, origin="current_owner_statement",
                          approval="approved_for_local_use", lifecycle="active", contradiction_refs=[])
            right["claims"].append(record)
        else:
            for i,state in enumerate(states):
                field_id = f"{field}_{i}"
                atoms.append({"atom_id":f"oracle-{family}-{i}", "field":field_id,
                              "kind":"accepted", "accepted":["yes"]})
                if state is None:
                    continue
                record = deepcopy(template)
                record.pop("complete", None)
                record.update(claim_id=f"oracle-{family}-{i}", dimension=field_id,
                              value="yes" if state else "no", origin="current_owner_statement",
                              approval="approved_for_local_use", lifecycle="active", contradiction_refs=[])
                right["claims"].append(record)
        left["intent"]["atoms"][family] = atoms
    return load_owner(left), load_owner(right)


def _execute_oracle(case: Mapping[str, Any], corpus: FixtureCorpus, as_of: datetime) -> dict[str, Any]:
    """Independently authored transformations for the documented oracle cases."""
    from dataclasses import replace
    from .claims import approve_claim, invalidate_source, resolve_usable, revise_claim
    from .consent import DisclosureGrant, claim_digest, project_for_peer
    from .ranking import evaluate_owner
    from .scoring import assess_direction, assess_pair_offline

    seeker, candidate = corpus.owner(case["seeker_id"]), corpus.owner(case["candidate_id"])
    code = case["test_id"]
    if code == "T-MATCH-001":
        pair = assess_pair_offline(seeker, candidate, as_of)
        result = _numbers(pair.a_to_b)
        result.update(reverse_F=_fraction_text(pair.b_to_a.preference_fit),
                      reverse_S=_fraction_text(pair.b_to_a.supported_fit),
                      offline_rank=_fraction_text(pair.reciprocal_supported_fit or Fraction(0)))
        result["_checks"] = {"bilateral_qualification": pair.qualifies}
        return result
    if code in {"T-MATCH-002", "T-MATCH-003", "T-MATCH-013"}:
        return _numbers(assess_direction(seeker, candidate, as_of))
    if code == "T-MATCH-004":
        raw = seeker.to_dict()
        raw["intent"]["expires_at"] = "2027-01-01T00:00:00Z"
        return _numbers(assess_direction(load_owner(raw), candidate, as_of))
    if code == "T-MATCH-005":
        raw = candidate.to_dict()
        for claim in raw["claims"]:
            if claim["dimension"] in {"offer.capabilities", "intent.problem_tags"}:
                claim["value"] = []
                claim["complete"] = True
            elif claim["dimension"] in {"workflow.rhythm", "workflow.cycle", "workflow.artifact", "workflow.venue", "communication.feedback"}:
                claim["value"] = "explicit-nonmatch"
        return _numbers(assess_direction(seeker, load_owner(raw), as_of))
    if code == "T-MATCH-006":
        raw = candidate.to_dict()
        fields = {atom.field for _, atoms in seeker.intent.atoms for atom in atoms}
        for claim in raw["claims"]:
            if claim["dimension"] in fields:
                claim["origin"] = "model_inferred"
        return _numbers(assess_direction(seeker, load_owner(raw), as_of))
    if code == "T-MATCH-007":
        # Only the reverse preferences change; the forward example is preserved.
        raw = candidate.to_dict()
        raw["intent"]["atoms"]["capability_complementarity"] = [
            {"atom_id":f"reverse-cap-{i}", "field":"offer.capabilities", "kind":"membership", "wanted":v}
            for i,v in enumerate(("budgeting", "event-coordination", "stage-design", "fundraising"))]
        raw["intent"]["atoms"]["practical_workflow_fit"] = [
            {"atom_id":"reverse-flow-1", "field":"workflow.rhythm", "kind":"accepted", "accepted":["async"]},
            {"atom_id":"reverse-flow-2", "field":"workflow.venue", "kind":"accepted", "accepted":["in_person"]}]
        raw["intent"]["atoms"]["communication_fit"] = [
            {"atom_id":"reverse-comm-1", "field":"communication.feedback", "kind":"accepted", "accepted":["direct"]},
            {"atom_id":"reverse-comm-2", "field":"communication.feedback", "kind":"accepted", "accepted":["gentle"]}]
        seeker = _changed_claim(seeker, "communication.feedback", origin="current_owner_statement")
        pair = assess_pair_offline(seeker, load_owner(raw), as_of)
        result = _numbers(pair.a_to_b)
        result.update(reverse_F=_fraction_text(pair.b_to_a.preference_fit),
                      reverse_S=_fraction_text(pair.b_to_a.supported_fit), pair_qualified=pair.qualifies)
        return result
    if code == "T-MATCH-008":
        raw = seeker.to_dict()
        raw["intent"]["hard_constraints"].append({"constraint_id":"required-project", "field":"intent.problem_tags",
                                                   "kind":"membership", "wanted":"portable-learning-exhibit"})
        candidate = _changed_claim(candidate, "intent.problem_tags", value=["unrelated-project"])
        return _numbers(assess_direction(load_owner(raw), candidate, as_of))
    if code == "T-MATCH-009":
        from copy import deepcopy
        data = _decode(resources.files("agentmatch").joinpath("data", "synthetic", "corpus.json").read_bytes())
        bad = deepcopy(data)
        bad["owners"][0]["claims"][0]["source_refs"] = ["lumen-conv-1:agent-1"]
        try:
            _validate_corpus(bad, corpus.manifest)
            agent_allowed = True
        except FixtureError as exc:
            agent_allowed = exc.code != "NON_OWNER_EVIDENCE_REFERENCE"
        before = assess_direction(seeker, candidate, as_of)
        raw = candidate.to_dict()
        for claim in raw["claims"]:
            if claim["dimension"] == "communication.feedback":
                claim["source_refs"].append("harbor-conv-4:owner-2")
        after = assess_direction(seeker, load_owner(raw), as_of)
        return {"assistant_claim_allowed":agent_allowed,
                "duplicated_event_changes_score":_numbers(before) != _numbers(after)}
    if code == "T-MATCH-010":
        result = _numbers(assess_direction(seeker, candidate, as_of))
        old = next(claim for claim in candidate.claims if claim.dimension == "intent.problem_tags")
        revised = revise_claim(candidate, old.claim_id, list(old.value), list(old.source_refs), as_of,
                               "Exact current correction in the synthetic oracle", origin="current_owner_statement")
        pending = resolve_usable(revised, as_of).by_id(old.claim_id)
        committed = approve_claim(revised, old.claim_id, old.version + 1)
        current = resolve_usable(committed, as_of).by_id(old.claim_id)
        grant = DisclosureGrant("oracle-stale-grant",old.claim_id,old.version,claim_digest(old),"collaboration",seeker.owner_id,
                                "oracle-topic",("dimension","value","complete"),(),as_of,as_of+timedelta(hours=1),"oracle-owner-review")
        try:
            project_for_peer(committed, grant, seeker.owner_id, "collaboration", as_of)
            stale_grant_denied = False
        except ValueError:
            stale_grant_denied = True
        result["_checks"] = {"new_version_unapproved":pending is None,
                             "approved_correction_has_new_version":current is not None and current.version == old.version + 1,
                             "old_grant_cannot_authorize_correction":stale_grant_denied}
        return result
    if code == "T-MATCH-011":
        result = _numbers(assess_direction(seeker, candidate, as_of))
        lumen, harbor = corpus.owner("lumen"), corpus.owner("harbor")
        withdrawn = invalidate_source(harbor, "harbor-conv-1:owner-1")
        changed = assess_direction(lumen, withdrawn, as_of)
        result["_checks"] = {"withdrawn_source_removes_capability_evidence":changed.coverage == Fraction(3,5)
                             and changed.status == "insufficient_evidence"}
        return result
    if code == "T-MATCH-012":
        before = assess_direction(seeker, candidate, as_of)
        after = assess_direction(seeker, replace(candidate, alias="A famous-looking fictional alias"), as_of)
        return {"score_changes":_numbers(before) != _numbers(after)}
    if code == "T-MATCH-014":
        candidate = replace(candidate, blocked_owner_ids=())
        seeker = replace(seeker, blocked_owner_ids=())
        before = assess_direction(seeker, candidate, as_of)
        changed = _changed_claim(candidate, "politics.affiliation", value="OTHER_DISABLED_SYNTHETIC_MARKER")
        after = assess_direction(seeker, changed, as_of)
        offer = next(claim for claim in candidate.claims if claim.dimension == "offer.capabilities")
        grant = DisclosureGrant("oracle-offer-grant",offer.claim_id,offer.version,claim_digest(offer),"collaboration",seeker.owner_id,
                                "oracle-offer",("dimension","value","complete"),(),as_of,as_of+timedelta(hours=1),"oracle-owner-review")
        wire = project_for_peer(candidate, grant, seeker.owner_id, "collaboration", as_of).to_dict()
        encoded = json.dumps(wire)
        secret = next(claim for claim in candidate.claims if claim.dimension == "politics.affiliation")
        sensitive_grant = replace(grant,claim_id=secret.claim_id,claim_version=secret.version,
                                  value_digest=claim_digest(secret),audience_alias="oracle-disabled")
        try:
            project_for_peer(candidate,sensitive_grant,seeker.owner_id,"collaboration",as_of)
            denied = False
        except ValueError:
            denied = True
        return {"sensitive_canary_outbound":"SYNTHETIC_DISABLED_SENSITIVE_CANARY" in encoded,
                "sensitive_changes_score":_numbers(before) != _numbers(after),
                "_checks":{"sensitive_projection_denied":denied,
                           "private_source_fields_absent":"source_refs" not in wire and "grove-conv" not in encoded}}
    if code == "T-MATCH-015":
        return {"offers":evaluate_owner(seeker,corpus.owners,as_of)["offered_owner_ids"]}
    if code == "T-MATCH-016":
        record = assess_pair_offline(seeker,candidate,as_of).to_dict()
        honest = record["scope"] == "offline_reference_only_not_a_verified_network_joint_score"
        return {"verified_remote_joint_score":not honest,
                "_checks":{"directional_reports_explicitly_offline":all(record[key]["mode"] == "offline_synthetic_reference" for key in ("a_to_b","b_to_a"))}}
    if code == "T-MATCH-017":
        left,right = _component_fixture(seeker,candidate,[True,True,True,False],[True],
                                        [False,None,None,None],[None])
        base = assess_direction(left,right,as_of)
        permuted = replace(right,claims=tuple(reversed(right.claims)))
        permuted_left = replace(left,intent=replace(left.intent,atoms=tuple((family,tuple(reversed(atoms))) for family,atoms in reversed(left.intent.atoms))))
        same = assess_direction(permuted_left,permuted,as_of)
        below_fit = assess_direction(left,right,as_of,threshold=Fraction(6000001,10000000))
        raw = left.to_dict()
        raw["intent"]["atoms"]["practical_workflow_fit"][0]["weight"] = "0.9999"
        below_coverage = assess_direction(load_owner(raw),right,as_of)
        return {"gate_exact":_fraction_text(base.coverage),
                "permutation_changes_result":_numbers(base) != _numbers(same),
                "_checks":{"exact_preference_gate":base.preference_fit == Fraction(3,5) and base.qualifies,
                           "just_below_fit_cannot_qualify":not below_fit.qualifies,
                           "just_below_coverage_cannot_qualify":below_coverage.coverage < Fraction(3,4) and not below_coverage.qualifies}}
    if code == "T-MATCH-018":
        before = assess_direction(seeker,candidate,as_of)
        after = assess_direction(seeker,_changed_claim(candidate,"communication.feedback",approval="unreviewed"),as_of)
        original = {(atom.family,atom.atom_id):atom.weight for atom in before.atoms}
        changed = {(atom.family,atom.atom_id):atom.weight for atom in after.atoms}
        return {"missing_candidate_renormalizes":original != changed,
                "_checks":{"coverage_lost_exactly_missing_weight":before.coverage-after.coverage == Fraction(1,10)}}
    if code == "T-MATCH-019":
        report = evaluate_owner(seeker,corpus.owners,as_of)
        return {"offer_allowed":candidate.owner_id in report["offered_owner_ids"],
                "_checks":{"self_not_offered":seeker.owner_id not in report["offered_owner_ids"],
                           "full_scan":report["scanned_candidates"] == len(corpus.owners)-1,
                           "population_order_invariant":report == evaluate_owner(seeker,reversed(corpus.owners),as_of)}}
    if code == "T-MATCH-020":
        left,right = _component_fixture(seeker,candidate,[True,True,None],[True],[True,True,None],[True,None])
        base = assess_direction(left,right,as_of)
        second_left,second_right = _component_fixture(seeker,candidate,[True,True,None],[True],[True,True,None],[None,None])
        changed = assess_direction(second_left,second_right,as_of)
        result = _numbers(base)
        result["communication_unknown_C"] = _fraction_text(changed.coverage)
        result["_checks"] = {"exact_boundary_passes":base.qualifies,
                             "below_coverage_abstains":changed.status == "insufficient_evidence"}
        return result
    if code == "T-SELECTIVITY-003":
        strict = assess_pair_offline(seeker,candidate,as_of,threshold_a="0.85",threshold_b="0.85")
        independent = assess_pair_offline(seeker,candidate,as_of,threshold_a="0.85",threshold_b="0.80")
        return {"thresholds":["0.85","0.85"],"pair_qualified":strict.qualifies,
                "independent_thresholds":["0.85","0.80"],"independent_pair_qualified":independent.qualifies}
    if code == "T-SELECTIVITY-004":
        changed = _changed_claim(candidate,"communication.feedback",approval="unreviewed")
        result = _numbers(assess_direction(seeker,changed,as_of,threshold="0.85"))
        result["threshold"] = "0.85"
        return result
    raise FixtureError("UNIMPLEMENTED_FIXTURE_ORACLE")


def run_oracles(as_of: datetime) -> list[dict[str, Any]]:
    """Execute authored expectations without altering or regenerating them.

    The results establish synthetic software behavior only. They do not certify
    encryption, model extraction, human usefulness, or a network deployment.
    """
    from .models import checked_time
    clock = checked_time(as_of)
    corpus = load_corpus()
    results: list[dict[str, Any]] = []
    for case in corpus.expected_outcomes:
        expected = case["expected"]
        try:
            observed = _execute_oracle(case,corpus,clock)
            additional_checks = observed.pop("_checks",{})
            comparisons = {key:observed.get(key) == value for key,value in expected.items()}
            passed = all(comparisons.values()) and all(additional_checks.values())
            details: dict[str, Any] = {"expectation_checks":comparisons, "additional_checks":additional_checks,
                                       "reasoning":case["reasoning"], "observed":observed}
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            passed = False
            details = {"error":str(exc).partition(":")[0], "reasoning":case["reasoning"]}
        results.append({"case_id":case["test_id"],"scenario_id":case["scenario_id"],
                        "passed":passed,"status":"passed" if passed else "failed",
                        "expected":expected,"details":details,"execution_kind":"synthetic_in_memory"})
    return results
