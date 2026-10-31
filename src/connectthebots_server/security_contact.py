"""RFC 9116 contact metadata with an explicit, operator-reviewed expiry.

The endpoint does not renew itself on requests or restarts. No fallback address
is invented; the operator must verify the configured role mailbox and set a new
expiry after review. The file is unsigned and advertises no encryption key.
"""

from datetime import datetime, timedelta
import re
from urllib.parse import quote, urlsplit

from starlette.responses import PlainTextResponse
from starlette.routing import Route

from .clock import Clock, timestamp
from .config import Settings


SECURITY_CONTACT_PATH = "/.well-known/security.txt"
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_MAILBOX = re.compile(rf"{_ATOM}(?:\.{_ATOM})*@{_LABEL}(?:\.{_LABEL})+")


def security_contact_routes(settings: Settings, clock: Clock) -> list[Route]:
    async def security_contact(request):
        expiry = settings.security_contact_expires
        now = clock()
        if (
            not _MAILBOX.fullmatch(settings.contact_email)
            or len(settings.contact_email) > 254
            or not isinstance(expiry, datetime)
            or expiry.tzinfo is None
            or expiry.utcoffset() is None
            or not now < expiry <= now + timedelta(days=365)
            or urlsplit(settings.public_base_url).scheme != "https"
            or request.url.scheme != "https"
        ):
            return PlainTextResponse("Security contact information is not currently available.\n", status_code=503)
        # Encode characters such as +, ?, &, # and % so the mailbox cannot
        # introduce mailto query fields or fragments. The origin is configured,
        # never reflected from Host, Forwarded or other request headers.
        contact = "mailto:" + quote(settings.contact_email, safe="@")
        body = (
            f"Contact: {contact}\n"
            f"Expires: {timestamp(expiry)}\n"
            f"Canonical: {settings.public_base_url}{SECURITY_CONTACT_PATH}\n"
            "Preferred-Languages: en\n"
        )
        return PlainTextResponse(body)

    return [Route(SECURITY_CONTACT_PATH, security_contact, methods=["GET"])]
