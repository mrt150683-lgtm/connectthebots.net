"""Permission, immutable version and allowlist tests for R0 projections."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import unittest

from agentmatch.claims import revise_claim, approve_claim, revoke_claim, resolve_usable
from agentmatch.consent import DisclosureGrant, claim_digest, project_for_peer
from agentmatch.fixtures import load_corpus
from agentmatch.models import load_owner, parse_time

NOW = parse_time("2026-10-25T12:00:00Z")


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.owner = load_corpus().owner("harbor")
        self.claim = next(claim for claim in self.owner.claims if claim.dimension == "communication.feedback")
        self.grant = DisclosureGrant("grant-1", self.claim.claim_id, self.claim.version,
                                     claim_digest(self.claim), "collaboration", "lumen", "audience-random-alias-1",
                                     ("dimension", "value", "provenance"), ("feedback-style",), NOW - timedelta(minutes=1),
                                     NOW + timedelta(minutes=5), "fictional-owner-approval-1")

    def project(self, grant=None, owner=None, **kwargs):
        return project_for_peer(owner or self.owner, grant or self.grant, kwargs.get("peer", "lumen"),
                                kwargs.get("purpose", "collaboration"), kwargs.get("as_of", NOW),
                                question_id=kwargs.get("question_id"))

    def test_T_PRIV_003_allowlist_omits_private_lineage_and_scores(self):
        peer = self.project().to_dict()
        self.assertEqual(set(peer), {"alias", "version", "dimension", "value", "purpose", "expires_at", "provenance"})
        self.assertEqual(peer["provenance"], "owner_reviewed_inference")
        self.assertNotIn(self.claim.claim_id, json.dumps(peer))
        for source in self.claim.source_refs:
            self.assertNotIn(source, json.dumps(peer))
        self.assertNotIn(claim_digest(self.claim), json.dumps(peer))

    def test_T_PRIV_002_exact_audience_purpose_and_question(self):
        for kwargs in ({"peer": "another-peer"}, {"purpose": "dating"}, {"question_id": "unapproved-question"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.project(**kwargs)
        self.assertEqual(self.project(question_id="feedback-style").value, "direct")

    def test_T_PRIV_002_version_value_and_context_are_bound(self):
        with self.assertRaisesRegex(ValueError, "STALE_DISCLOSURE"):
            self.project(grant=replace(self.grant, claim_version=self.claim.version + 1))
        with self.assertRaisesRegex(ValueError, "STALE_DISCLOSURE"):
            self.project(grant=replace(self.grant, value_digest="0" * 64))
        for field, value in (("value", "different"), ("context", "new interpretation"), ("source_refs", ("different-source",))):
            changed = replace(self.owner, claims=tuple(replace(claim, **{field: value}) if claim.claim_id == self.claim.claim_id else claim for claim in self.owner.claims))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "STALE_DISCLOSURE"):
                self.project(owner=changed)

    def test_T_PRIV_002_expiry_revocation_no_clock_grace(self):
        for grant in (replace(self.grant, expires_at=NOW), replace(self.grant, revoked=True), replace(self.grant, issued_at=NOW+timedelta(seconds=1))):
            with self.assertRaisesRegex(ValueError, "GRANT_INACTIVE"):
                self.project(grant=grant)
        with self.assertRaisesRegex(ValueError, "STALE_DISCLOSURE"):
            self.project(owner=revoke_claim(self.owner, self.claim.claim_id))

    def test_T_PRIV_002_changed_claim_has_no_inherited_disclosure(self):
        revised = revise_claim(self.owner, self.claim.claim_id, "direct", ["fictional-new-source"], NOW)
        reviewed = approve_claim(revised, self.claim.claim_id, self.claim.version + 1)
        with self.assertRaisesRegex(ValueError, "STALE_DISCLOSURE"):
            self.project(owner=reviewed)

    def test_T_MATCH_014_sensitive_requires_registry_and_exact_permissions(self):
        data = self.owner.to_dict()
        claim = self.claim.to_dict()
        claim.update(claim_id="sensitive-politics", dimension="politics.affiliation", value="FICTIONAL-SENSITIVE-CANARY", origin="current_owner_statement")
        data["claims"].append(claim)
        owner = load_owner(data)
        target = next(claim for claim in owner.claims if claim.claim_id == "sensitive-politics")
        grant = replace(self.grant, claim_id=target.claim_id, claim_version=target.version, value_digest=claim_digest(target))
        with self.assertRaisesRegex(ValueError, "STALE_DISCLOSURE"):
            self.project(owner=owner, grant=grant)
        data["permissions"] = [{"family": "politics", "purpose": "collaboration", "operation": "local_use"}]
        owner = load_owner(data)
        with self.assertRaisesRegex(ValueError, "DISCLOSURE_NOT_PERMITTED"):
            self.project(owner=owner, grant=grant)
        data["permissions"].append({"family": "politics", "purpose": "collaboration", "operation": "disclosure"})
        owner = load_owner(data)
        self.assertEqual(self.project(owner=owner, grant=grant).value, "FICTIONAL-SENSITIVE-CANARY")
        data["claims"][-1]["dimension"] = "politics.invented_axis"
        owner = load_owner(data)
        self.assertFalse(any(claim.claim_id == "sensitive-politics" for claim in resolve_usable(owner, NOW).claims))

    def test_T_PRIV_003_nested_injection_unknown_fields_and_private_alias(self):
        for field in ("source_refs", "source_text", "embedding", "score", "context"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "INVALID_PROJECTION_FIELDS"):
                self.project(grant=replace(self.grant, permitted_fields=("dimension", "value", field)))
        for alias in (self.claim.claim_id, self.claim.source_refs[0]):
            with self.assertRaisesRegex(ValueError, "PRIVATE_IDENTIFIER"):
                self.project(grant=replace(self.grant, audience_alias=alias))
        # A direct dataclass constructor is not a way around the typed value parser.
        injected = replace(self.claim, value={"source_text": "PRIVATE CANARY"})
        owner = replace(self.owner, claims=tuple(injected if claim.claim_id == injected.claim_id else claim for claim in self.owner.claims))
        with self.assertRaises(ValueError):
            self.project(owner=owner, grant=replace(self.grant, value_digest=claim_digest(injected)))

    def test_block_and_inactive_intent_cancel_disclosure(self):
        with self.assertRaisesRegex(ValueError, "PEER_BLOCKED"):
            self.project(owner=replace(self.owner, blocked_owner_ids=("lumen",)))
        with self.assertRaisesRegex(ValueError, "INTENT_INACTIVE"):
            self.project(owner=replace(self.owner, intent=replace(self.owner.intent, current=False)))

    def test_unapproved_claim_is_not_exportable_by_having_grant(self):
        changed = replace(self.owner, claims=tuple(replace(claim, approval="unreviewed") if claim.claim_id == self.claim.claim_id else claim for claim in self.owner.claims))
        with self.assertRaisesRegex(ValueError, "STALE_DISCLOSURE"):
            self.project(owner=changed)

    def test_complete_set_without_flag_is_incomplete_projection(self):
        target = next(claim for claim in self.owner.claims if claim.dimension == "offer.capabilities")
        grant = replace(self.grant, claim_id=target.claim_id, claim_version=target.version, value_digest=claim_digest(target))
        self.assertIsNone(self.project(grant=grant).complete)
        with_complete = replace(grant, permitted_fields=("dimension", "value", "complete", "provenance"))
        self.assertIs(self.project(grant=with_complete).complete, True)

    def test_grant_integer_fields_do_not_accept_boolean(self):
        with self.assertRaisesRegex(ValueError, "INTEGER_OUT_OF_RANGE"):
            self.project(grant=replace(self.grant, claim_version=True))


if __name__ == "__main__":
    unittest.main()
