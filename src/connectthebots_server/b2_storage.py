"""Bounded Backblaze B2 native transport for private recovery objects.

There is deliberately no deletion API and no retry of an uncertain upload.
The caller authenticates recovery records and decides whether a version is
eligible; provider timestamps and mutable file names are not that authority.
"""
import base64
from hashlib import sha1
import hmac
import json
import logging
import re
from urllib.parse import parse_qsl, quote, unquote, urlsplit


AUTH_URL = "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"
CAPABILITIES = {"listBuckets", "listFiles", "readFiles", "writeFiles"}
MAX_OBJECT_BYTES = 128 * 1024 * 1024
MAX_VERSIONS = 100000
MAX_PAGES = 1000
logger = logging.getLogger("connectthebots_server")


def _log_refusal(response):
    """Log only the HTTP status and B2's short machine code, such as
    transaction_cap_exceeded, so the operator can tell a quota refusal from an
    outage. Never the body, URL, headers, tokens or object names."""
    code = "unknown"
    try:
        raw = b""
        for chunk in response.iter_bytes(chunk_size=2048):
            raw += chunk
            if len(raw) >= 2048:
                break
        value = json.loads(raw[:2048])
        if isinstance(value, dict) and isinstance(value.get("code"), str) and re.fullmatch(r"[a-z_]{1,64}", value["code"]):
            code = value["code"]
    except Exception:
        pass
    logger.error("Recovery storage refused a request: HTTP %d, provider code %s", response.status_code, code)


class B2StorageError(ValueError):
    """Fixed messages only; provider bodies and credentials are never exposed."""

    @property
    def detail(self):
        return str(self)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value) is not None


def _name(value):
    return (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}", value) is not None
            and all(part not in {"", ".", ".."} for part in value.rstrip("/").split("/")))


def _metadata(value):
    if not isinstance(value, dict) or len(value) > 12:
        raise B2StorageError("The storage object metadata is invalid or too large.")
    size = 0
    for key, item in value.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", key)
                or not isinstance(item, str) or len(item) > 1500
                or any(not 32 <= ord(char) <= 126 for char in item)):
            raise B2StorageError("The storage object metadata is invalid or too large.")
        size += len(key) + len(quote(item, safe=""))
    if size > 1500:
        raise B2StorageError("The storage object metadata is invalid or too large.")
    return dict(value)


def _provider_url(value, *, upload=False, bucket_id=None):
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        allowed = host.endswith(".backblazeb2.com") or (upload and host.endswith(".backblaze.com"))
        if (url.scheme != "https" or not allowed or url.port not in {None, 443}
                or url.username is not None or url.password is not None or url.fragment or "#" in value
                or "\\" in value or any(ord(char) < 33 for char in value)
                or not re.fullmatch(r"[a-z0-9.-]+", host)):
            raise ValueError
        if upload:
            if not _identifier(bucket_id):
                raise ValueError
            if url.path == "/b2api/v4/b2_upload_file":
                pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
                if len(pairs) != 2 or {key for key, _ in pairs} != {"cvt", "bucket"}:
                    raise ValueError
                query = dict(pairs)
                if query["bucket"] != bucket_id or not _identifier(query["cvt"]):
                    raise ValueError
            else:
                match = re.fullmatch(r"/b2api/v4/b2_upload_file/([A-Za-z0-9_-]{1,512})/([A-Za-z0-9_-]{1,512})", url.path)
                if url.query or match is None or match.group(1) != bucket_id:
                    raise ValueError
        elif url.path not in {"", "/"} or url.query:
            raise ValueError
        return value if upload else value.rstrip("/")
    except (ValueError, TypeError, AttributeError):
        raise B2StorageError("The storage provider returned an invalid HTTPS endpoint.") from None


class B2Storage:
    def __init__(self, key_id, application_key, bucket_id, prefix, *, transport=None):
        if (not _identifier(key_id) or not _identifier(bucket_id) or not _name(prefix)
                or not prefix.endswith("/") or not isinstance(application_key, str)
                or not 1 <= len(application_key) <= 512
                or any(not 33 <= ord(char) <= 126 for char in application_key)):
            raise B2StorageError("Configure a dedicated storage key, bucket and private object prefix.")
        import httpx
        self._httpx = httpx
        self._key_id, self._application_key = key_id, application_key
        self.bucket_id, self.prefix = bucket_id, prefix
        self._client = httpx.Client(transport=transport, timeout=15.0, follow_redirects=False,
                                    trust_env=False, verify=True, headers={"Accept-Encoding": "identity"})

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method, url, *, limit, **kwargs):
        try:
            with self._client.stream(method, url, **kwargs) as response:
                if response.status_code != 200:
                    _log_refusal(response)
                    raise B2StorageError("The storage request was refused or its result is uncertain.")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise B2StorageError("The storage response encoding is unsupported.")
                declared = response.headers.get("content-length")
                if declared is not None and (len(declared) > 20 or not declared.isascii() or not declared.isdigit() or int(declared) > limit):
                    raise B2StorageError("The storage response exceeds its permitted size.")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise B2StorageError("The storage response exceeds its permitted size.")
                if declared is not None and len(body) != int(declared):
                    raise B2StorageError("The storage response is incomplete.")
                return bytes(body), response.headers
        except self._httpx.HTTPError as error:
            logger.error("Recovery storage request failed: %s", type(error).__name__)
            raise B2StorageError("The storage request failed or its result is uncertain; no retry was made.") from None

    def _json(self, method, url, *, limit=262144, **kwargs):
        body, _ = self._request(method, url, limit=limit, **kwargs)
        try:
            result = json.loads(body, object_pairs_hook=_unique_fields)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise B2StorageError("The storage provider returned an invalid response.") from None

    def _authorize(self):
        # Authorize anew for each operation: no stale token cache, wall-clock
        # decisions, credential refresh retries or hidden access expansion.
        basic = base64.b64encode((self._key_id + ":" + self._application_key).encode()).decode("ascii")
        result = self._json("GET", AUTH_URL, headers={"Authorization": "Basic " + basic})
        try:
            storage = result["apiInfo"]["storageApi"]
            allowed = storage["allowed"]
            buckets, capabilities = allowed["buckets"], allowed["capabilities"]
            if (not isinstance(buckets, list) or len(buckets) != 1 or buckets[0]["id"] != self.bucket_id
                    or not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities)
                    or set(capabilities) != CAPABILITIES or len(capabilities) != len(CAPABILITIES)
                    or allowed["namePrefix"] != self.prefix):
                raise ValueError
            token = result["authorizationToken"]
            if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError
            api, download, account_id = _provider_url(storage["apiUrl"]), _provider_url(storage["downloadUrl"]), result["accountId"]
            if not _identifier(account_id):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise B2StorageError("Storage access must be restricted to the exact bucket and prefix with bucket listing and file list, read and write only.") from None
        self._verify_bucket(api, token, account_id)
        return api, download, token

    def _verify_bucket(self, api, token, account_id):
        """Do not trust a prefix-only key to protect against bucket expiry rules.

        The writer cannot change policy. Recheck the independently administered
        bucket on every operation, including replacement-host downloads.
        """
        result = self._json("POST", api + "/b2api/v4/b2_list_buckets",
                            headers={"Authorization": token}, json={"accountId": account_id, "bucketId": self.bucket_id})
        try:
            buckets = result["buckets"]
            if (not isinstance(buckets, list) or len(buckets) != 1 or buckets[0]["bucketId"] != self.bucket_id
                    or buckets[0]["accountId"] != account_id or buckets[0]["bucketType"] != "allPrivate"):
                raise ValueError
            rules = buckets[0]["lifecycleRules"]
            if not isinstance(rules, list) or len(rules) > 100:
                raise ValueError
            fields = {"fileNamePrefix", "daysFromHidingToDeleting", "daysFromUploadingToHiding",
                      "daysFromStartingToCancelingUnfinishedLargeFiles"}
            for rule in rules:
                if not isinstance(rule, dict) or not set(rule) <= fields:
                    raise ValueError
                rule_prefix = rule["fileNamePrefix"]
                if not _name(rule_prefix) or not rule_prefix.startswith(self.prefix + "snapshots/"):
                    raise ValueError
                # A global, authority-specific or other unexpected expiry policy
                # is refused. Only reviewed snapshot-prefix rules are supported.
                for name, value in rule.items():
                    if name != "fileNamePrefix" and value is not None and (type(value) is not int or value <= 0):
                        raise ValueError
        except (KeyError, TypeError, ValueError):
            raise B2StorageError("Recovery storage must be private and lifecycle expiry must apply only to its snapshots prefix.") from None

    def _within(self, name):
        if not _name(name) or not name.startswith(self.prefix):
            raise B2StorageError("The requested storage object is outside the configured prefix.")

    def list_versions(self, prefix):
        """Return every version, including hide markers; never silently truncate."""
        self._within(prefix)
        api, _, token = self._authorize()
        payload = {"bucketId": self.bucket_id, "prefix": prefix, "maxFileCount": 1000}
        entries, seen_ids, cursors = [], set(), set()
        previous_name = ""
        for _ in range(MAX_PAGES):
            page = self._json("POST", api + "/b2api/v4/b2_list_file_versions", limit=4 * 1024 * 1024,
                              headers={"Authorization": token}, json=payload)
            try:
                files = page["files"]
                if not isinstance(files, list) or len(files) > 1000:
                    raise ValueError
                for item in files:
                    name, file_id, action, length = item["fileName"], item["fileId"], item["action"], item["contentLength"]
                    if (not _name(name) or not name.startswith(prefix) or name < previous_name
                            or not _identifier(file_id) or file_id in seen_ids
                            or item["bucketId"] != self.bucket_id or action not in {"upload", "hide", "start"}
                            or type(length) is not int or not 0 <= length <= MAX_OBJECT_BYTES
                            or (action != "upload" and length != 0)):
                        raise ValueError
                    entries.append({"name": name, "id": file_id, "action": action, "length": length,
                                    "metadata": _metadata(item.get("fileInfo", {}))})
                    seen_ids.add(file_id)
                    previous_name = name
                if len(entries) > MAX_VERSIONS:
                    raise ValueError
                next_name, next_id = page["nextFileName"], page["nextFileId"]
                if next_name is None and next_id is None:
                    return entries
                if (not files or not _name(next_name) or not next_name.startswith(prefix)
                        or next_name < previous_name or not _identifier(next_id)
                        or next_id in seen_ids or (next_name, next_id) in cursors):
                    raise ValueError
                cursors.add((next_name, next_id))
                payload.update(startFileName=next_name, startFileId=next_id)
            except (KeyError, TypeError, ValueError):
                raise B2StorageError("The complete storage version listing could not be verified.") from None
        raise B2StorageError("The storage version listing exceeds its supported limit.")

    def read(self, file_id, max_bytes=MAX_OBJECT_BYTES):
        if not _identifier(file_id) or type(max_bytes) is not int or not 0 <= max_bytes <= MAX_OBJECT_BYTES:
            raise B2StorageError("The requested storage download is invalid.")
        _, download, token = self._authorize()
        content, headers = self._request("POST", download + "/b2api/v4/b2_download_file_by_id", limit=max_bytes,
                                        headers={"Authorization": token}, json={"fileId": file_id})
        name = unquote(headers.get("x-bz-file-name", ""))
        self._within(name)
        digest = headers.get("x-bz-content-sha1", "")
        if (headers.get("x-bz-file-id") != file_id or not re.fullmatch(r"[a-f0-9]{40}", digest)
                or not hmac.compare_digest(sha1(content).hexdigest(), digest)):
            raise B2StorageError("The downloaded storage object failed validation.")
        return content

    def write(self, name, data, *, metadata=None):
        self._within(name)
        metadata = _metadata({} if metadata is None else metadata)
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_OBJECT_BYTES or name.endswith("/"):
            raise B2StorageError("The requested storage upload is invalid or too large.")
        api, _, token = self._authorize()
        upload = self._json("POST", api + "/b2api/v4/b2_get_upload_url",
                            headers={"Authorization": token}, json={"bucketId": self.bucket_id})
        try:
            if upload["bucketId"] != self.bucket_id:
                raise ValueError
            upload_url = _provider_url(upload["uploadUrl"], upload=True, bucket_id=self.bucket_id)
            upload_token = upload["authorizationToken"]
            if not isinstance(upload_token, str) or not 1 <= len(upload_token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in upload_token):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise B2StorageError("The storage provider returned invalid upload authorization.") from None
        digest = sha1(data).hexdigest()
        result = self._json("POST", upload_url, headers={
            "Authorization": upload_token, "X-Bz-File-Name": quote(name, safe="/"),
            "Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
            "X-Bz-Content-Sha1": digest,
            **{"X-Bz-Info-" + key: quote(value, safe="") for key, value in metadata.items()},
        }, content=data)
        if (result.get("bucketId") != self.bucket_id or result.get("fileName") != name
                or result.get("action") != "upload" or type(result.get("contentLength")) is not int
                or result["contentLength"] != len(data) or result.get("contentSha1") != digest
                or result.get("fileInfo", {}) != metadata
                or not _identifier(result.get("fileId"))):
            raise B2StorageError("The uploaded storage object could not be verified; no retry was made.")
        return result["fileId"]
