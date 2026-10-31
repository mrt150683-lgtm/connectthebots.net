"""Fictional B2 protocol responses, bounded IO and credential-safe failures."""
import copy
from hashlib import sha1
import importlib.util
import json
import unittest
from unittest.mock import patch
from urllib.parse import quote

from connectthebots_server.b2_storage import AUTH_URL, B2Storage, B2StorageError, _provider_url

HAS_HTTPX = importlib.util.find_spec("httpx") is not None
if HAS_HTTPX:
    import httpx


PREFIX = "fictional-recovery/"
BUCKET = "fictional-bucket"
KEY = "fictional-storage-key"
AUTH = {
    "accountId": "fictional-account",
    "authorizationToken": "fictional-authorized-token",
    "apiInfo": {"storageApi": {
        "apiUrl": "https://api001.backblazeb2.com",
        "downloadUrl": "https://f001.backblazeb2.com",
        "allowed": {"buckets": [{"id": BUCKET, "name": "fictional-bucket"}],
                    "capabilities": ["listBuckets", "listFiles", "readFiles", "writeFiles"], "namePrefix": PREFIX},
    }},
}


def version(name="authority/0000.json", file_id="version-0", action="upload", length=8):
    return {"fileName": PREFIX + name, "fileId": file_id, "action": action,
            "contentLength": length, "bucketId": BUCKET}


def page(files=None, next_name=None, next_id=None):
    return {"files": files or [], "nextFileName": next_name, "nextFileId": next_id}


@unittest.skipUnless(HAS_HTTPX, "server extra is not installed")
class B2StorageTests(unittest.TestCase):
    def client(self, handler, auth=None, bucket=None):
        self.requests = []
        def route(request):
            self.requests.append(request)
            if str(request.url) == AUTH_URL:
                return httpx.Response(200, json=auth if auth is not None else AUTH)
            if request.url.path.endswith("b2_list_buckets"):
                self.assertEqual(json.loads(request.content), {"accountId": "fictional-account", "bucketId": BUCKET})
                return httpx.Response(200, json={"buckets": [bucket if bucket is not None else {
                    "accountId": "fictional-account", "bucketId": BUCKET, "bucketType": "allPrivate", "lifecycleRules": []}]})
            return handler(request)
        result = B2Storage("fictional-key-id", KEY, BUCKET, PREFIX, transport=httpx.MockTransport(route))
        self.addCleanup(result.close)
        return result

    def test_complete_paginated_listing_includes_hide_markers(self):
        responses = [page([version()], PREFIX + "authority/0001.json", "version-1"),
                     page([version("authority/0001.json", "version-1", "hide", 0)])]
        payloads = []
        def handle(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json=responses.pop(0))
        result = self.client(handle).list_versions(PREFIX + "authority/")
        self.assertEqual(result, [
            {"name": PREFIX + "authority/0000.json", "id": "version-0", "action": "upload", "length": 8, "metadata": {}},
            {"name": PREFIX + "authority/0001.json", "id": "version-1", "action": "hide", "length": 0, "metadata": {}},
        ])
        self.assertEqual(payloads[1]["startFileName"], PREFIX + "authority/0001.json")
        self.assertEqual(payloads[1]["startFileId"], "version-1")
        self.assertEqual(payloads[0]["maxFileCount"], 1000)

    def test_versions_with_the_same_name_are_preserved_for_authority_review(self):
        client = self.client(lambda request: httpx.Response(200, json=page([version(), version(file_id="version-other")])))
        self.assertEqual(len(client.list_versions(PREFIX)), 2)

    def test_malformed_duplicate_outside_or_unordered_listing_refused(self):
        bad = [
            {}, {"files": {}}, page([version(), version()]),
            page([dict(version(), bucketId="different")]),
            page([dict(version(), fileName="outside/authority.json")]),
            page([dict(version(), contentLength=True)]),
            page([dict(version(), action="folder")]),
            page([version("authority/0001.json"), version("authority/0000.json", "version-1")]),
            page([version()], PREFIX + "authority/0000.json", "version-0"),
            page([version()], PREFIX + "authority/0001.json", None),
        ]
        for response in bad:
            with self.subTest(response=response):
                client = self.client(lambda request: httpx.Response(200, json=response))
                with self.assertRaises(B2StorageError):
                    client.list_versions(PREFIX)

    def test_pagination_is_bounded_and_never_returns_a_partial_listing(self):
        client = self.client(lambda request: httpx.Response(200, json=page([version()], PREFIX + "authority/0001.json", "version-1")))
        with patch("connectthebots_server.b2_storage.MAX_PAGES", 1):
            with self.assertRaises(B2StorageError):
                client.list_versions(PREFIX)
        client = self.client(lambda request: httpx.Response(200, json=page([version()])))
        with patch("connectthebots_server.b2_storage.MAX_VERSIONS", 0):
            with self.assertRaises(B2StorageError):
                client.list_versions(PREFIX)

    def test_least_privilege_exact_bucket_prefix_and_capabilities_required(self):
        changes = [
            {"buckets": None}, {"buckets": [{"id": BUCKET}, {"id": "another"}]},
            {"buckets": [{"id": "another"}]}, {"namePrefix": ""},
            {"namePrefix": PREFIX + "narrower/"},
            {"capabilities": ["listBuckets", "listFiles", "readFiles", "writeFiles", "deleteFiles"]},
            {"capabilities": ["listBuckets", "listFiles", "readFiles", "writeFiles", "writeBuckets"]},
            {"capabilities": ["listFiles", "readFiles", "writeFiles"]},
            {"capabilities": ["listFiles", "writeFiles"]},
        ]
        for change in changes:
            with self.subTest(change=change):
                auth = copy.deepcopy(AUTH)
                auth["apiInfo"]["storageApi"]["allowed"].update(change)
                client = self.client(lambda request: self.fail("must not send a scoped request"), auth)
                with self.assertRaises(B2StorageError):
                    client.list_versions(PREFIX)

    def test_provider_endpoints_cannot_redirect_credentials_to_other_origins(self):
        for endpoint in ["http://api001.backblazeb2.com", "https://backblazeb2.com.evil.test", "https://evil.test",
                         "https://api001.backblazeb2.com:8443", "https://user@api001.backblazeb2.com",
                         "https://api001.backblazeb2.com/path", "https://api001.backblazeb2.com?token=x"]:
            with self.subTest(endpoint=endpoint):
                auth = copy.deepcopy(AUTH)
                auth["apiInfo"]["storageApi"]["apiUrl"] = endpoint
                client = self.client(lambda request: self.fail("must not contact untrusted endpoint"), auth)
                with self.assertRaises(B2StorageError):
                    client.list_versions(PREFIX)

    def test_redirects_and_provider_error_bodies_are_not_followed_or_disclosed(self):
        client = self.client(lambda request: httpx.Response(307, headers={"Location": "https://evil.test/"}, content=KEY.encode()))
        with self.assertRaises(B2StorageError) as raised:
            client.list_versions(PREFIX)
        self.assertNotIn(KEY, str(raised.exception))
        self.assertEqual(len(self.requests), 3)
        self.assertNotIn(KEY, repr(client))

    def test_refusals_log_only_status_and_provider_code(self):
        # A free account's daily cap must be distinguishable from an outage in
        # the operator log, without the body, token, key or object names.
        storage = self.client(lambda request: httpx.Response(403, json={
            "status": 403, "code": "transaction_cap_exceeded", "message": "fictional-private-detail " + KEY}))
        with self.assertLogs("connectthebots_server", level="ERROR") as logs, self.assertRaises(B2StorageError):
            storage.list_versions(PREFIX + "authority/")
        joined = " ".join(logs.output)
        self.assertIn("HTTP 403, provider code transaction_cap_exceeded", joined)
        for secret in ("fictional-private-detail", KEY, "fictional-authorized-token", PREFIX):
            self.assertNotIn(secret, joined)
        odd = self.client(lambda request: httpx.Response(500, content=b'{"code": "<script>"}'))
        with self.assertLogs("connectthebots_server", level="ERROR") as logs, self.assertRaises(B2StorageError):
            odd.list_versions(PREFIX + "authority/")
        self.assertIn("provider code unknown", " ".join(logs.output))

    def test_json_size_encoding_and_content_length_are_bounded(self):
        for response in [httpx.Response(200, headers={"content-length": "5000000"}, content=b"{}"),
                         httpx.Response(200, headers={"content-length": "1"}, content=b"{}"),
                         httpx.Response(200, content=b"not json"),
                         httpx.Response(200, content=b'{"files": [], "files": [], "nextFileName": null, "nextFileId": null}'),
                         httpx.Response(200, headers={"Content-Encoding": "unrecognized"}, content=b"{}")]:
            client = self.client(lambda request: response)
            with self.assertRaises(B2StorageError):
                client.list_versions(PREFIX)

    def test_read_checks_file_identity_name_scope_size_and_digest(self):
        content = b"fictional authenticated bytes"
        headers = {"X-Bz-File-Id": "version-0", "X-Bz-File-Name": PREFIX + "authority/0000.json",
                   "X-Bz-Content-Sha1": sha1(content).hexdigest()}
        client = self.client(lambda request: httpx.Response(200, content=content, headers=headers))
        self.assertEqual(client.read("version-0", max_bytes=100), content)
        for change in [{"X-Bz-File-Id": "wrong"}, {"X-Bz-File-Name": "outside/object"},
                       {"X-Bz-Content-Sha1": "0" * 40}, {"X-Bz-Content-Sha1": "none"}]:
            with self.subTest(change=change):
                client = self.client(lambda request: httpx.Response(200, content=content, headers=headers | change))
                with self.assertRaises(B2StorageError):
                    client.read("version-0", max_bytes=100)
        client = self.client(lambda request: httpx.Response(200, content=content, headers=headers))
        with self.assertRaises(B2StorageError):
            client.read("version-0", max_bytes=5)

    def test_chunked_response_cannot_exceed_download_limit(self):
        class Chunks(httpx.SyncByteStream):
            def __iter__(self):
                yield b"1234"
                yield b"5678"
        client = self.client(lambda request: httpx.Response(200, stream=Chunks()))
        with self.assertRaises(B2StorageError):
            client.read("version-0", max_bytes=5)

    def test_upload_sends_exact_bytes_and_verifies_provider_receipt(self):
        content, name = b"fictional encrypted snapshot", PREFIX + "backups/snapshot.ctb"
        uploaded = []
        def handle(request):
            if request.url.path.endswith("b2_get_upload_url"):
                return httpx.Response(200, json={"bucketId": BUCKET,
                    "uploadUrl": "https://pod-001.backblaze.com/b2api/v4/b2_upload_file/fictional-bucket/slot",
                    "authorizationToken": "fictional-upload-token"})
            uploaded.append(request)
            return httpx.Response(200, json={"bucketId": BUCKET, "fileName": name, "action": "upload",
                "contentLength": len(content), "contentSha1": sha1(content).hexdigest(), "fileId": "uploaded-version"})
        client = self.client(handle)
        self.assertEqual(client.write(name, content), "uploaded-version")
        self.assertEqual(uploaded[0].content, content)
        self.assertEqual(uploaded[0].headers["Content-Length"], str(len(content)))
        self.assertEqual(uploaded[0].headers["X-Bz-Content-Sha1"], sha1(content).hexdigest())
        self.assertEqual(uploaded[0].headers["X-Bz-File-Name"], name)
        self.assertEqual(uploaded[0].headers["Authorization"], "fictional-upload-token")

    def test_documented_query_upload_url_sends_exact_bucket_and_bytes(self):
        documented = "https://pod-000-1005-03.backblaze.com/b2api/v4/b2_upload_file?cvt=c001_v0001005_t0027&bucket=4a48fe8875c6214145260818"
        self.assertEqual(_provider_url(documented, upload=True, bucket_id="4a48fe8875c6214145260818"), documented)
        upload_url = documented.replace("4a48fe8875c6214145260818", BUCKET)
        content, name, uploaded = b"fictional encrypted bytes", PREFIX + "snapshots/snapshot.ctb", []
        def handle(request):
            if request.url.path.endswith("b2_get_upload_url"):
                return httpx.Response(200, json={"bucketId": BUCKET, "uploadUrl": upload_url,
                                                "authorizationToken": "fictional-upload-token"})
            uploaded.append(request)
            return httpx.Response(200, json={"bucketId": BUCKET, "fileName": name, "action": "upload",
                "contentLength": len(content), "contentSha1": sha1(content).hexdigest(), "fileId": "uploaded-version"})
        client = self.client(handle)
        self.assertEqual(client.write(name, content), "uploaded-version")
        self.assertEqual(str(uploaded[0].url), upload_url)
        self.assertEqual(uploaded[0].content, content)

    def test_upload_url_duplicate_extra_wrong_bucket_or_fragment_refused(self):
        endpoint = "https://pod-000-1005-03.backblaze.com/b2api/v4/b2_upload_file"
        valid_query = "?cvt=c001_v0001005_t0027&bucket=" + BUCKET
        invalid = [
            endpoint + valid_query + "&bucket=" + BUCKET,
            endpoint + "?cvt=first&cvt=second",
            endpoint + valid_query + "&extra=unexpected",
            endpoint + "?cvt=c001_v0001005_t0027&bucket=wrong",
            endpoint + "?cvt=&bucket=" + BUCKET,
            endpoint + valid_query + "#fragment",
            endpoint + valid_query + "#",
            endpoint + "/wrong/slot",
            endpoint + "/" + BUCKET + "/slot" + valid_query,
        ]
        for upload_url in invalid:
            with self.subTest(upload_url=upload_url):
                def handle(request):
                    self.assertTrue(request.url.path.endswith("b2_get_upload_url"), "invalid upload URL must not be contacted")
                    return httpx.Response(200, json={"bucketId": BUCKET, "uploadUrl": upload_url,
                                                    "authorizationToken": "fictional-upload-token"})
                with self.assertRaises(B2StorageError):
                    self.client(handle).write(PREFIX + "authority/0000.json", b"fictional")

    def test_upload_timeout_after_acceptance_is_not_retried(self):
        uploads = []
        def handle(request):
            if request.url.path.endswith("b2_get_upload_url"):
                return httpx.Response(200, json={"bucketId": BUCKET,
                    "uploadUrl": "https://pod-001.backblaze.com/b2api/v4/b2_upload_file/fictional-bucket/slot",
                    "authorizationToken": "fictional-upload-token"})
            uploads.append(request.content)
            raise httpx.ReadTimeout(KEY, request=request)
        client = self.client(handle)
        with self.assertRaises(B2StorageError) as raised:
            client.write(PREFIX + "authority/0000.json", b"fictional marker")
        self.assertEqual(len(uploads), 1)
        self.assertNotIn(KEY, str(raised.exception))

    def test_marker_metadata_is_bounded_encoded_and_round_tripped(self):
        metadata = {"ctb-authority": '{"generation":0,"marker":"fictional"}'}
        data, name = b"fictional marker", PREFIX + "authority/0000.json"
        def handle(request):
            if request.url.path.endswith("b2_list_file_versions"):
                return httpx.Response(200, json=page([dict(version(), fileInfo=metadata)]))
            if request.url.path.endswith("b2_get_upload_url"):
                return httpx.Response(200, json={"bucketId": BUCKET,
                    "uploadUrl": "https://pod-001.backblaze.com/b2api/v4/b2_upload_file/fictional-bucket/slot",
                    "authorizationToken": "fictional-upload-token"})
            self.assertEqual(request.headers["X-Bz-Info-ctb-authority"], quote(metadata["ctb-authority"], safe=""))
            return httpx.Response(200, json={"bucketId": BUCKET, "fileName": name, "action": "upload",
                "contentLength": len(data), "contentSha1": sha1(data).hexdigest(), "fileId": "uploaded-version",
                "fileInfo": metadata})
        client = self.client(handle)
        self.assertEqual(client.list_versions(PREFIX)[0]["metadata"], metadata)
        self.assertEqual(client.write(name, data, metadata=metadata), "uploaded-version")
        before = len(self.requests)
        for invalid in [{"ctb-authority": "x" * 1501}, {"Upper-Key": "no"}, {"bad": "line\nbreak"}, {"ctb-authority": "é"}]:
            with self.assertRaises(B2StorageError):
                client.write(name, data, metadata=invalid)
        self.assertEqual(len(self.requests), before)

    def test_invalid_upload_host_receipt_or_metadata_never_retries(self):
        for variant in ("host", "receipt", "metadata"):
            with self.subTest(variant=variant):
                uploaded = []
                def handle(request):
                    if request.url.path.endswith("b2_get_upload_url"):
                        host = "evil.test" if variant == "host" else "pod-001.backblaze.com"
                        return httpx.Response(200, json={"bucketId": BUCKET,
                            "uploadUrl": "https://" + host + "/b2api/v4/b2_upload_file/fictional-bucket/slot",
                            "authorizationToken": "fictional-upload-token"})
                    uploaded.append(request)
                    return httpx.Response(200, json={"bucketId": BUCKET, "fileName": PREFIX + "authority/0000.json",
                        "action": "upload", "contentLength": 1, "contentSha1": sha1(b"x").hexdigest(),
                        "fileId": "uploaded-version", "fileInfo": {"unexpected": "entry"} if variant == "metadata" else {},
                        **({"contentSha1": "0" * 40} if variant == "receipt" else {})})
                client = self.client(handle)
                with self.assertRaises(B2StorageError):
                    client.write(PREFIX + "authority/0000.json", b"x")
                self.assertEqual(len(uploaded), 0 if variant == "host" else 1)

    def test_no_requests_for_invalid_local_objects(self):
        client = self.client(lambda request: self.fail("no request expected"))
        for name in ["outside/file", PREFIX + "../file", PREFIX + "a//b", PREFIX + "file%2fother"]:
            with self.subTest(name=name):
                with self.assertRaises(B2StorageError):
                    client.write(name, b"fictional")
        with self.assertRaises(B2StorageError):
            client.read("version-0", max_bytes=True)
        self.assertEqual(self.requests, [])

    def test_fresh_authorization_for_each_operation(self):
        client = self.client(lambda request: httpx.Response(200, json=page()))
        client.list_versions(PREFIX)
        client.list_versions(PREFIX)
        self.assertEqual(sum(str(request.url) == AUTH_URL for request in self.requests), 2)
        self.assertEqual(sum(request.url.path.endswith("b2_list_buckets") for request in self.requests), 2)

    def test_private_bucket_and_nonexpiring_authority_are_verified_before_io(self):
        base = {"accountId": "fictional-account", "bucketId": BUCKET, "bucketType": "allPrivate", "lifecycleRules": []}
        changes = [
            {"bucketType": "allPublic"}, {"bucketId": "wrong-bucket"}, {"accountId": "wrong-account"},
            {"lifecycleRules": None},
            {"lifecycleRules": [{"fileNamePrefix": "", "daysFromUploadingToHiding": 1}]},
            {"lifecycleRules": [{"fileNamePrefix": PREFIX, "daysFromHidingToDeleting": 1}]},
            {"lifecycleRules": [{"fileNamePrefix": PREFIX + "authority/", "daysFromHidingToDeleting": 1}]},
            {"lifecycleRules": [{"fileNamePrefix": PREFIX + "authority/000", "daysFromUploadingToHiding": 1}]},
            {"lifecycleRules": [{"fileNamePrefix": PREFIX + "snapshots/", "daysFromUploadingToHiding": True}]},
        ]
        for change in changes:
            with self.subTest(change=change):
                client = self.client(lambda request: self.fail("must not issue object IO"), bucket=base | change)
                with self.assertRaises(B2StorageError):
                    client.list_versions(PREFIX)
        safe = base | {"lifecycleRules": [{"fileNamePrefix": PREFIX + "snapshots/",
                                         "daysFromUploadingToHiding": 13, "daysFromHidingToDeleting": 1}]}
        client = self.client(lambda request: httpx.Response(200, json=page()), bucket=safe)
        self.assertEqual(client.list_versions(PREFIX), [])


if __name__ == "__main__":
    unittest.main()
