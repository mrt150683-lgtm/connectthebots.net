"""Hostile field coverage at real HTTP/rendering boundaries, with fictional data."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import importlib.util
import io
import logging
from pathlib import Path
import re
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) for name in ("starlette", "httpx", "jwt", "jinja2"))
if AVAILABLE:
    from starlette.testclient import TestClient
    from connectthebots_server.app import create_app
    from connectthebots_server.config import Settings
    from connectthebots_server.sessions import SESSION_COOKIE, storage_time


PAYLOADS = ("<script>x</script>", "javascript:x", "[x](javascript:x)")


FIRST_PARTY_SCRIPTS = {"/assets/share.js", "/assets/tuning.js", "/assets/nav.js"}


class RenderedHTML(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.elements, self.words = [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.words.append(data)


@unittest.skipUnless(AVAILABLE, "optional server extra not installed")
class HostileFieldMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        settings = Settings.from_env({
            "APP_ENV": "staging", "PUBLIC_BASE_URL": "https://matrix.example.test",
            "DATABASE_PATH": str(Path(self.temp.name) / "app.db"),
            "SESSION_SECRET": "fictional-matrix-" + "x" * 40,
            "GOOGLE_CLIENT_ID": "fictional-client", "GOOGLE_CLIENT_SECRET": "fictional-secret",
            "ACCOUNT_SIGNIN_ENABLED": "true", "SIGNUPS": "open",
        })
        self.app = create_app(settings, clock=lambda: self.now)
        self.client = self.enterContext(TestClient(self.app, base_url=settings.public_base_url))
        self.auth, self.db = self.app.state.auth, self.app.state.database
        self.board, self.reports = self.app.state.board, self.app.state.reports
        self.tokens = {}
        with self.db.transaction() as connection:
            for owner in ("ama", "joon", "sora"):
                connection.execute(
                    "INSERT INTO users(id,google_sub,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?,?)",
                    (owner, "fictional-" + owner, owner + "@example.test", owner,
                     storage_time(self.now), storage_time(self.now)))
                self.tokens[owner] = self.auth.sessions.create(connection, owner)
        self.use("ama")

    def use(self, owner):
        self.client.cookies.clear()
        self.client.cookies.set(SESSION_COOKIE, self.tokens[owner])
        self.csrf = self.auth.sessions.current(self.tokens[owner], refresh=False).csrf

    def post(self, path, **values):
        return self.client.post(path, data={"csrf": self.csrf, **values}, follow_redirects=False)

    def field(self, response, name):
        self.assertEqual(response.status_code, 200)
        found = re.search(r'name="' + name + r'" value="([^"]+)"', response.text)
        self.assertIsNotNone(found)
        return unescape(found.group(1))

    def inert(self, response, *texts, status=200):
        self.assertEqual(response.status_code, status)
        rendered = RenderedHTML(response.text)
        visible = "".join(rendered.words)
        attributes = [value for _, attrs in rendered.elements for value in attrs.values() if value]
        for value in texts:
            self.assertTrue(value in visible or value in attributes, "Submitted value was not shown as plain text")
        for tag, attrs in rendered.elements:
            if tag == "script" and attrs.get("src") in FIRST_PARTY_SCRIPTS:
                continue  # our own static file, never built from submitted data
            self.assertNotIn(tag, {"script", "iframe", "object", "embed"})
            self.assertFalse(any(name.startswith("on") for name in attrs))
            for name in ("href", "src", "action", "formaction"):
                if name in attrs:
                    self.assertFalse(attrs[name].lower().startswith(("javascript:", "data:")))
        return rendered

    @staticmethod
    def posting(value="A careful collaboration"):
        return {"text": "I build tools. I seek help. I offer " + value + ".",
                "tags": [value], "looking_for": value, "offering": value}

    def publish(self, owner):
        draft = self.board.save_draft(owner, self.posting())
        return self.board.publish(owner, draft["revision"], draft["payload_hash"])

    @staticmethod
    def report(kind, value):
        result = {"kind": kind, "checked_at": "2026-10-30T12:00:00Z", "posts_read": 1,
                  "places_checked": [{"name": "place " + value, "url": "https://board.example.test/"}]}
        if kind == "question":
            result[kind] = {"text": "question " + value, "why": "why " + value}
        else:
            fields = ("who", "why_now", "they_bring", "they_could_gain", "i_bring", "i_could_gain", "still_unknown", "first_step")
            result[kind] = {name: name + " " + value for name in fields}
            result[kind].update(where="https://board.example.test/fictional", evidence=[{
                "quote_or_link": "quote " + value, "matches": "matches " + value}])
        return result

    @staticmethod
    def paste(payload):
        lines = ["Kind: " + payload["kind"], "Checked at: " + payload["checked_at"], "Posts read: 1"]
        lines += ["Places checked: " + place["name"] + " | " + place["url"] for place in payload["places_checked"]]
        if payload["kind"] == "question":
            lines += ["Question: " + payload["question"]["text"], "Why: " + payload["question"]["why"]]
        else:
            value = payload["connection"]
            lines += ["Connection: " + value["who"] + " | " + value["where"]]
            labels = {"why_now": "Why now", "they_bring": "They bring", "they_could_gain": "They could gain",
                      "i_bring": "I bring", "i_could_gain": "I could gain", "still_unknown": "Still unknown", "first_step": "Small first step"}
            lines += [label + ": " + value[key] for key, label in labels.items()]
            lines += ["Evidence: " + item["quote_or_link"] + " | " + item["matches"] for item in value["evidence"]]
        return "\n".join(lines)

    def test_profile_and_key_labels_are_inert_in_text_and_attribute_contexts(self):
        for index, value in enumerate(PAYLOADS):
            with self.subTest(value=value):
                with self.db.transaction() as connection:
                    connection.execute("UPDATE users SET display_name=? WHERE id=?", (value, "ama"))
                self.inert(self.client.get("/app/account"), value)
                self.assertEqual(self.post("/app/keys", name=value).status_code, 200)
                key = self.reports.list_keys("ama")[index]
                self.inert(self.client.get("/app/keys"), value)
                renamed = '\" autofocus onfocus=\"x ' + value
                self.assertEqual(self.post("/app/keys/" + key["id"] + "/rename", name=renamed).status_code, 303)
                self.inert(self.client.get("/app/keys"), renamed)

    def test_every_report_narrative_is_inert_in_paste_preview_and_stored_dashboard(self):
        key = self.reports.create_key("ama", "Fictional matrix")
        for kind in ("question", "connection"):
            for index, value in enumerate(PAYLOADS):
                with self.subTest(kind=kind, value=value):
                    payload = self.report(kind, value)
                    details = payload[kind]
                    words = [payload["places_checked"][0]["name"]]
                    words += [text for name, text in details.items() if isinstance(text, str) and name != "where"]
                    if kind == "connection":
                        words += list(details["evidence"][0].values())
                    self.inert(self.post("/app/reports/preview", report_text=self.paste(payload)), *words)
                    response = self.client.post("/api/v1/reports", json=payload, headers={
                        "Authorization": "Bearer " + key["key"], "Idempotency-Key": kind + str(index)})
                    self.assertEqual(response.status_code, 201)
                    self.inert(self.client.get("/app"), *words)

    def test_report_links_refuse_active_schemes_and_escape_attribute_breakout(self):
        key = self.reports.create_key("ama", "Fictional links")
        paths = (("places_checked", 0, "url"), ("connection", "where"), ("connection", "evidence", 0, "quote_or_link"))
        for path in paths:
            for value in ("javascript:alert(1)", "data:text/html,<script>x</script>", "[x](javascript:x)"):
                with self.subTest(path=path, value=value):
                    payload = self.report("connection", "Plain text")
                    target = payload
                    for part in path[:-1]:
                        target = target[part]
                    target[path[-1]] = value
                    response = self.client.post("/api/v1/reports", json=payload, headers={
                        "Authorization": "Bearer " + key["key"], "Idempotency-Key": "link-test"})
                    # Evidence can contain Markdown as an inert quotation, never an active link.
                    if path[-1] == "quote_or_link" and value.startswith("["):
                        self.assertEqual(response.status_code, 201)
                        self.inert(self.client.get("/app"), value)
                    else:
                        self.assertEqual(response.status_code, 422)
                        self.assertNotIn(value, response.text)
        payload = self.report("connection", "Plain text")
        url = 'https://board.example.test/\"><script>x</script>'
        payload["places_checked"][0]["url"] = url
        payload["connection"]["where"] = url
        payload["connection"]["evidence"][0]["quote_or_link"] = url
        preview = self.post("/app/reports/preview", report_text=self.paste(payload))
        rendered = self.inert(preview, url)
        self.assertEqual(sum(attrs.get("href") == url for _, attrs in rendered.elements), 2)

    def test_posting_text_tags_offers_needs_and_search_are_inert(self):
        for value in PAYLOADS:
            with self.subTest(value=value):
                payload = self.posting(value)
                self.assertEqual(self.post("/app/posting", **{**payload, "tags": value}).status_code, 303)
                self.inert(self.client.get("/app/posting"), *payload["tags"], payload["text"])
                preview = self.post("/app/posting/preview")
                self.inert(preview, value, payload["text"])
                token = self.field(preview, "publication_token")
                result = self.post("/app/posting/publish", publication_token=token)
                self.assertEqual(result.status_code, 303)
                self.inert(self.client.get(result.headers["location"]), value, payload["text"])
                self.inert(self.client.get("/board", params={"q": value}), value)
                self.assertEqual(self.client.get(result.headers["location"] + ".json").json()["text"], payload["text"])

    def test_introduction_note_reason_and_each_message_remain_plain_text_for_peer(self):
        self.publish("ama")
        target = self.publish("joon")
        value = ' '.join(PAYLOADS)
        preview = self.post("/app/introductions/preview", target_slug=target["slug"], note=value, reason=value)
        self.inert(preview, value)
        result = self.post("/app/introductions", request_token=self.field(preview, "request_token"))
        self.assertEqual(result.status_code, 303)
        path = result.headers["location"]
        self.use("joon")
        detail = self.client.get(path)
        self.inert(detail, value)
        accepted = self.post(path + "/accept", accept_token=self.field(detail, "accept_token"))
        self.assertEqual(accepted.status_code, 303)
        conversation = accepted.headers["location"]
        for value in PAYLOADS:
            token = self.field(self.client.get(conversation), "message_token")
            self.assertEqual(self.post(conversation + "/messages", text=value, message_token=token).status_code, 303)
        self.use("ama")
        self.inert(self.client.get(conversation), *PAYLOADS)

    def test_moderation_details_are_private_data_and_never_echoed_as_html(self):
        target = self.publish("joon")
        for value in PAYLOADS:
            form = self.client.get("/app/report", params={"kind": "posting", "target": target["slug"]})
            result = self.post("/app/report", report_token=self.field(form, "report_token"), reason="other", details=value)
            if value.startswith("<"):
                self.assertEqual(result.status_code, 422)
            else:
                self.inert(result)
            self.assertNotIn(value, result.text)
        own = self.post("/app/account/export").json()
        self.assertEqual({item["details"] for item in own["abuse_reports"]}, set(PAYLOADS[1:]))
        self.use("joon")
        peer = self.post("/app/account/export").json()
        self.assertEqual(peer["abuse_reports"], [])
        self.inert(self.client.get("/board/" + target["slug"]))

    def test_typed_controls_and_form_errors_refuse_hostile_values_without_echo(self):
        for value in PAYLOADS:
            for field, defaults in (("paused", {"paused": "false", "cadence": "none"}),
                                    ("cadence", {"paused": "false", "cadence": "none"})):
                result = self.post("/app/settings", **{**defaults, field: value})
                self.assertEqual(result.status_code, 422)
                self.assertNotIn(value, result.text)
            for path, fields in (("/app/account/delete", {"confirmation": value}),
                                 ("/app/notifications/preferences", {"new_message": value})):
                with self.subTest(path=path, value=value):
                    result = self.post(path, **fields)
                    self.assertIn(result.status_code, (400, 422))
                    self.assertNotIn(value, result.text)
            for name in ("page", "before"):
                path = "/board" if name == "page" else "/app/conversations/invalid"
                result = self.client.get(path, params={name: value})
                self.assertIn(result.status_code, (400, 404))
                self.assertNotIn(value, result.text)

    def test_report_paste_errors_retain_hostile_text_only_in_escaped_editor(self):
        expiry = self.auth.sessions.current(self.tokens["ama"], refresh=False).expires_at
        self.now += timedelta(hours=1)
        for value in (*PAYLOADS, '</textarea><img src="x" onerror="fixture()">'):
            with self.subTest(value=value):
                result = self.post("/app/reports/preview", report_text=value)
                self.assertEqual(result.status_code, 422)
                self.inert(result, value, status=422)
                editor = re.search(r'<textarea\b[^>]*name="report_text"[^>]*>([\s\S]*?)</textarea>', result.text)
                self.assertIsNotNone(editor)
                self.assertEqual(unescape(editor.group(1)), value)
                # The submitted text must occur only as escaped editor data,
                # never in an error, executable element or URL attribute.
                surrounding = result.text[:editor.start()] + result.text[editor.end():]
                self.assertNotIn(value, unescape(surrounding))
                self.assertEqual(self.reports.list_reports("ama"), [])
                self.assertEqual(self.auth.sessions.current(self.tokens["ama"], refresh=False).expires_at, expiry)

    def test_report_type_timestamp_count_and_slug_refuse_hostile_values_without_persistence(self):
        key = self.reports.create_key("ama", "Typed controls")
        for name in ("kind", "checked_at", "posts_read", "board_slug"):
            for value in PAYLOADS:
                with self.subTest(name=name, value=value):
                    payload = self.report("connection", "Plain")
                    target = payload["connection"] if name == "board_slug" else payload
                    target[name] = value
                    result = self.client.post("/api/v1/reports", json=payload, headers={
                        "Authorization": "Bearer " + key["key"], "Idempotency-Key": "typed-test"})
                    self.assertEqual(result.status_code, 422)
                    self.assertNotIn(value, result.text)
        self.assertEqual(self.reports.list_reports("ama"), [])

    def test_waitlist_email_consent_invite_and_removal_refuse_hostile_fields(self):
        self.auth.settings = replace(self.auth.settings, signups="closed")
        csrf = self.field(self.client.get("/signin"), "csrf")
        for value in PAYLOADS:
            for path, fields, expected in (
                ("/auth/waitlist", {"email": value, "consent": "yes"}, 422),
                ("/auth/waitlist", {"email": "fictional@example.test", "consent": value}, 422),
                ("/auth/google/start", {"invite": value}, 400),
                ("/auth/waitlist/remove", {"removal": value}, 422),
            ):
                with self.subTest(path=path, fields=fields):
                    result = self.client.post(path, data={"csrf": csrf, **fields}, follow_redirects=False)
                    self.assertEqual(result.status_code, expected)
                    self.assertNotIn(value, result.text)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM oauth_attempts").fetchone()[0], 0)

    def test_enabled_requests_do_not_log_profile_report_message_or_credentials(self):
        self.publish("ama")
        target = self.publish("joon")
        interactions = self.app.state.interactions
        prepared = interactions.prepare_request("ama", target["slug"], "A small trial?", "Shared research")
        introduction = interactions.request("ama", prepared, "fictional-log-canary-nonce")
        conversation = interactions.accept("joon", introduction["id"], introduction["context_hash"])["conversation"]
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger()
        previous_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            created = self.post("/app/keys", name="Fictional logging check")
            self.assertEqual(created.status_code, 200)
            key = re.search(r"ctb_[A-Z2-7]{52}", created.text).group(0)
            report_canary = "PRIVATE_REPORT_CANARY_R72"
            response = self.client.post("/api/v1/reports", json=self.report("question", report_canary), headers={
                "Authorization": "Bearer " + key, "Idempotency-Key": "logging-check"})
            self.assertEqual(response.status_code, 201)
            self.assertIn(report_canary, self.client.get("/app").text)
            path = "/app/conversations/" + conversation["id"]
            token = self.field(self.client.get(path), "message_token")
            message_canary = "PRIVATE_MESSAGE_CANARY_M38"
            self.assertEqual(self.post(path + "/messages", message_token=token, text=message_canary).status_code, 303)
            self.assertIn(message_canary, self.client.get(path).text)
            self.assertEqual(self.post("/app/reports/preview", report_text=report_canary).status_code, 422)
            self.assertEqual(self.post("/app/account/export").status_code, 200)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            handler.close()
        captured = output.getvalue()
        for private in (key, self.tokens["ama"], self.csrf, "ama@example.test", report_canary, message_canary):
            self.assertNotIn(private, captured)


if __name__ == "__main__":
    unittest.main()
