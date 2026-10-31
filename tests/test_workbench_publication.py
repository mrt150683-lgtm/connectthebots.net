"""Workbench publication, visibility and privacy invariants (fictional data only)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from agentmatch.consent import DisclosureGrant
from agentmatch_workbench.adapters.seed import build_service, load_seed
from agentmatch_workbench.application.service import WorkbenchError
from agentmatch_workbench.domain.briefs import BriefValidationError, parse_brief
from agentmatch_workbench.domain.publication import (PublicationError, authorize_publication, prepare_publication,
                                                     project_notice)

CANARY = "CANARY-private-7f3a9c-never-share"
OTHER_CANARY = "CANARY-juniper-41be02-never-share"
ALL_SECTIONS = ["working_arrangement", "feedback", "schedule", "commercial_note"]


def publish(service, owner, board="community-tools", include=None, days=30):
    preview = service.preview_publication(owner, {"board_id": board, "expires_in_days": days,
                                                  "include": ALL_SECTIONS if include is None else include})
    service.approve_publication(owner, {"board_id": board, "expires_at": preview["expires_at"],
                                        "include": preview["include"], "approval_digest": preview["approval_digest"]})
    return preview


def edit_brief(service, owner, **changes):
    brief = service.store.briefs[owner]
    content = {**brief.content(), **changes}
    return service.save_brief(owner, {"expected_version": brief.version, "brief": content})


def search_aliases(service, owner, **query):
    response = service.search(owner, {"text": "", "limit": 20, **query})
    return [item["card"]["publisher_alias"] for item in response["results"]]


class PublicationApprovalTests(unittest.TestCase):
    def setUp(self):
        self.service = build_service()

    def test_editing_brief_invalidates_pending_publication_approval(self):
        preview = self.service.preview_publication("marlow", {"board_id": "community-tools", "expires_in_days": 14, "include": ALL_SECTIONS})
        edit_brief(self.service, "marlow", title="Maintenance planner, revised scope")
        with self.assertRaises(WorkbenchError) as raised:
            self.service.approve_publication("marlow", {"board_id": "community-tools", "expires_at": preview["expires_at"],
                                                        "include": preview["include"], "approval_digest": preview["approval_digest"]})
        self.assertEqual(raised.exception.code, "APPROVAL_STALE")
        self.assertEqual(self.service.store.notices_by_owner("marlow"), ())

    def test_changing_preview_settings_invalidates_approval(self):
        preview = self.service.preview_publication("marlow", {"board_id": "community-tools", "expires_in_days": 14, "include": ALL_SECTIONS})
        with self.assertRaises(WorkbenchError) as raised:  # same digest, narrower sections: different exact payload
            self.service.approve_publication("marlow", {"board_id": "community-tools", "expires_at": preview["expires_at"],
                                                        "include": ["feedback"], "approval_digest": preview["approval_digest"]})
        self.assertEqual(raised.exception.code, "APPROVAL_STALE")

    def test_editing_brief_after_publication_suspends_live_notice_until_reapproved(self):
        publish(self.service, "marlow")
        self.assertIn("Marlow", search_aliases(self.service, "juniper"))
        edit_brief(self.service, "marlow", basis="volunteer", accepted_bases=["volunteer"])
        self.assertNotIn("Marlow", search_aliases(self.service, "juniper"))
        status = self.service.workspace("marlow")["publications"][0]["status"]
        self.assertEqual(status, "suspended_brief_changed")
        publish(self.service, "marlow")
        notice = self.service.store.notices_by_owner("marlow")[0]
        self.assertEqual((notice.revision, notice.payload["basis"]), (2, "volunteer"))
        self.assertIn("Marlow", search_aliases(self.service, "juniper"))

    def test_identical_resubmission_does_not_create_a_version(self):
        brief = self.service.store.briefs["marlow"]
        self.service.save_brief("marlow", {"expected_version": brief.version, "brief": brief.content()})
        self.assertEqual(self.service.store.briefs["marlow"].version, brief.version)

    def test_peer_disclosure_grant_cannot_authorize_board_publication(self):
        brief = self.service.store.briefs["marlow"]
        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        request = prepare_publication(brief, publisher_alias="Marlow", board_id="community-tools",
                                      board_member_ids=self.service.store.boards["community-tools"].member_ids,
                                      include=(), expires_at=now + timedelta(days=7), now=now)
        disclosure = DisclosureGrant("g-1", "marlow-offers", 1, request.payload_digest, "collaboration", "juniper",
                                     "peer-offers", ("dimension", "value"), (), now, now + timedelta(days=1), "owner-approval-1")
        with self.assertRaises(PublicationError) as raised:
            authorize_publication(disclosure, payload=request.payload, owner_id="marlow", board_id="community-tools", now=now)
        self.assertEqual(raised.exception.code, "PUBLICATION_GRANT_REQUIRED")

    def test_board_grant_rejects_payload_it_did_not_approve(self):
        publish(self.service, "marlow")
        notice = self.service.store.notices_by_owner("marlow")[0]
        grant = self.service.store.grants[notice.grant_id]
        tampered = {**notice.payload, "title": "Something the owner never approved"}
        with self.assertRaises(PublicationError) as raised:
            authorize_publication(grant, payload=tampered, owner_id="marlow", board_id="community-tools", now=self.service.now())
        self.assertEqual(raised.exception.code, "PAYLOAD_NOT_APPROVED")
        # A tampered board record is also refused at read time.
        self.service.store.notices[notice.notice_id] = replace(notice, payload=tampered)
        self.assertNotIn("Marlow", search_aliases(self.service, "juniper"))


class VisibilityTests(unittest.TestCase):
    """Unauthorized, expired, revoked or blocked notices cannot be retrieved or used."""

    def setUp(self):
        self.service = build_service()
        publish(self.service, "marlow")
        publish(self.service, "juniper")

    def card(self, viewer, alias):
        for item in self.service.search(viewer, {"text": "", "limit": 20})["results"]:
            if item["card"]["publisher_alias"] == alias:
                return item["card"]
        raise AssertionError(f"{alias} not visible to {viewer}")

    def assert_unusable(self, viewer, card):
        ref = {"notice_id": card["notice_id"], "revision": card["revision"]}
        for call in (lambda: self.service.explain(viewer, ref),
                     lambda: self.service.propose_introduction(viewer, {**ref, "first_step": "scoping-call", "note": ""}),
                     lambda: self.service.block_notice(viewer, ref)):
            with self.assertRaises(WorkbenchError) as raised:
                call()
            self.assertEqual(raised.exception.code, "NOTICE_UNAVAILABLE")

    def test_outside_audience_board_is_never_retrieved_or_usable(self):
        self.assertIn("Fen", search_aliases(self.service, "juniper"))  # juniper belongs to the makers board
        self.assertNotIn("Fen", search_aliases(self.service, "marlow"))
        fen = self.card("juniper", "Fen")
        self.assert_unusable("marlow", fen)

    def test_member_added_after_approval_is_outside_the_fixed_audience(self):
        board = self.service.store.boards["makers"]
        self.service.store.boards["makers"] = replace(board, member_ids=tuple(sorted(board.member_ids + ("marlow",))))
        self.assertNotIn("Fen", search_aliases(self.service, "marlow"))

    def test_expired_notice_is_not_retrieved_even_though_the_index_still_has_it(self):
        quill = next(n for n in self.service.store.notices.values() if n.owner_id == "quill")
        self.assertIn(quill.notice_id, self.service.index._documents)  # stale derived entry is deliberate
        self.assertNotIn("Quill", search_aliases(self.service, "marlow"))
        self.assert_unusable("marlow", {"notice_id": quill.notice_id, "revision": quill.revision})

    def test_stale_card_after_withdrawal_cannot_be_used(self):
        card = self.card("juniper", "Marlow")
        notice = self.service.store.notices[card["notice_id"]]
        index_entry = self.service.index._documents[notice.notice_id]
        self.service.withdraw_notice("marlow", {"notice_id": notice.notice_id})
        self.service.index._documents[notice.notice_id] = index_entry  # simulate an index that missed the withdrawal
        self.assertNotIn("Marlow", search_aliases(self.service, "juniper"))
        self.assert_unusable("juniper", card)

    def test_stale_card_after_expiry_cannot_be_used(self):
        card = self.card("juniper", "Marlow")
        self.service.clock.advance(timedelta(days=31))
        self.assertNotIn("Marlow", search_aliases(self.service, "juniper"))
        self.assert_unusable("juniper", card)

    def test_blocked_pair_cannot_see_or_use_each_other(self):
        card = self.card("juniper", "Marlow")
        reverse = self.card("marlow", "Juniper")
        self.service.block_notice("marlow", {"notice_id": reverse["notice_id"], "revision": reverse["revision"]})
        self.assertNotIn("Marlow", search_aliases(self.service, "juniper"))
        self.assertNotIn("Juniper", search_aliases(self.service, "marlow"))
        self.assert_unusable("juniper", card)

    def test_old_revision_card_is_refused_after_republication(self):
        card = self.card("juniper", "Marlow")
        edit_brief(self.service, "marlow", hours_per_week=6)
        publish(self.service, "marlow")
        self.assert_unusable("juniper", card)
        self.assertEqual(self.card("juniper", "Marlow")["revision"], card["revision"] + 1)

    def test_own_notice_is_not_returned_to_its_publisher(self):
        self.assertNotIn("Marlow", search_aliases(self.service, "marlow"))

    def test_search_is_bounded_and_says_it_is_not_exhaustive(self):
        response = self.service.search("juniper", {"text": "", "limit": 5})
        self.assertLessEqual(len(response["results"]), 5)
        self.assertIn("not a list of the best possible matches", response["coverage"]["statement"])
        with self.assertRaises(WorkbenchError):
            self.service.search("juniper", {"text": "", "limit": 500})
        with self.assertRaises(WorkbenchError):
            self.service.search("juniper", {"text": "", "limit": 5, "board_ids": ["makers"]})

    def test_no_match_search_returns_an_empty_reason(self):
        response = self.service.search("marlow", {"text": "zeppelin", "limit": 10})
        self.assertEqual((response["results"], response["empty_reason"]), ([], "no_results"))


class PrivacyCanaryTests(unittest.TestCase):
    """Private canary values never enter publication, index or peer-facing payloads."""

    def test_canary_never_leaves_its_owner(self):
        service = build_service()
        edit_brief(service, "marlow", private_notes=CANARY, commercial_note="")
        edit_brief(service, "juniper", private_notes=OTHER_CANARY)
        # Even with every optional section included, the canary cannot be published.
        preview = publish(service, "marlow")
        publish(service, "juniper")
        outward = [preview["payload"], preview["card"]]
        outward += [dict(n.payload) for n in service.store.notices.values()]
        outward += [doc.text for doc, _ in service.index._documents.values()]
        juniper_views = [service.workspace("juniper"), service.search("juniper", {"text": "", "limit": 20})]
        card = next(r["card"] for r in juniper_views[1]["results"] if r["card"]["publisher_alias"] == "Marlow")
        juniper_views.append(service.explain("juniper", {"notice_id": card["notice_id"], "revision": card["revision"]}))
        service.propose_introduction("juniper", {"notice_id": card["notice_id"], "revision": card["revision"],
                                                 "first_step": "async-review", "note": "Hello"})
        juniper_views.append(service.workspace("juniper"))
        for owner in ("sable", "wren", "teasel", "calloway", "brook", "fen", "quill"):
            juniper_views.append(service.workspace(owner))
            juniper_views.append(service.search(owner, {"text": "", "limit": 20}))
        text = json.dumps(outward + juniper_views, ensure_ascii=False)
        self.assertNotIn(CANARY, text)
        # Juniper's own canary appears only in Juniper's own brief view.
        self.assertIn(OTHER_CANARY, json.dumps(service.workspace("juniper")["brief"]))
        self.assertNotIn(OTHER_CANARY, json.dumps(service.workspace("marlow")))
        # Marlow's private selectivity and capacity are not in any peer record either.
        for record in [dict(n.payload) for n in service.store.notices.values()]:
            self.assertFalse({"private_notes", "selectivity", "capacity"} & set(record))

    def test_peer_views_carry_no_claim_ids_or_source_refs(self):
        service = build_service()
        publish(service, "marlow")
        publish(service, "juniper")
        card = next(r["card"] for r in service.search("juniper", {"text": "", "limit": 20})["results"] if r["card"]["publisher_alias"] == "Marlow")
        explanation = json.dumps(service.explain("juniper", {"notice_id": card["notice_id"], "revision": card["revision"]}))
        for forbidden in ("local_claim_refs", "claim_id", "source_refs", "marlow-brief", "numerator", "supported_fit"):
            self.assertNotIn(forbidden, explanation)

    def test_unselected_optional_sections_are_not_published(self):
        brief = build_service().store.briefs["marlow"]
        payload = project_notice(brief, publisher_alias="Marlow", include=())
        self.assertFalse({"working_arrangement", "feedback", "schedule", "commercial_note", "private_notes"} & set(payload))


class BriefValidationTests(unittest.TestCase):
    def test_both_entry_paths_validate_from_the_fictional_examples(self):
        seed = load_seed()
        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        for mode in ("project", "contribute"):
            brief = parse_brief(seed["examples"][mode], owner_id="example", version=1, updated_at=now)
            self.assertEqual(brief.mode, mode)

    def test_all_field_errors_are_reported_and_unknown_fields_refused(self):
        seed = load_seed()
        now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        bad = {**seed["examples"]["project"], "title": "", "primary_need": "", "focus_areas": ["not-a-tag"]}
        with self.assertRaises(BriefValidationError) as raised:
            parse_brief(bad, owner_id="example", version=1, updated_at=now)
        self.assertEqual({e["field"] for e in raised.exception.errors}, {"title", "primary_need", "focus_areas"})
        with self.assertRaises(BriefValidationError) as raised:
            parse_brief({**seed["examples"]["project"], "free_text_evidence": "I am the best"}, owner_id="example", version=1, updated_at=now)
        self.assertEqual(raised.exception.errors[0]["code"], "UNKNOWN_FIELD")

    def test_version_conflict_is_refused(self):
        service = build_service()
        brief = service.store.briefs["marlow"]
        with self.assertRaises(WorkbenchError) as raised:
            service.save_brief("marlow", {"expected_version": brief.version + 5, "brief": brief.content()})
        self.assertEqual(raised.exception.code, "VERSION_CONFLICT")


if __name__ == "__main__":
    unittest.main()
