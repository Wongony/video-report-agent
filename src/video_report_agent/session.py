"""Signed persistent anonymous owner cookies."""

import hashlib
import hmac
import os
import secrets
from http.cookies import CookieError, SimpleCookie


class OwnerSessions:
    def __init__(self, root, *, secure=False):
        path = root / ".session-key"
        if not path.exists():
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write(secrets.token_bytes(32))
        self.key = path.read_bytes()
        self.secure = secure

    def _sign(self, owner):
        return hmac.new(self.key, owner.encode(), hashlib.sha256).hexdigest()

    def identify(self, header):
        cookie = SimpleCookie()
        try:
            cookie.load(header or "")
            token = cookie["vr_owner"].value
            owner, signature = token.split(".")
            if len(owner) == 32 and hmac.compare_digest(signature, self._sign(owner)):
                return owner, None
        except (CookieError, KeyError, ValueError):
            pass
        owner = secrets.token_hex(16)
        cookie = SimpleCookie()
        cookie["vr_owner"] = f"{owner}.{self._sign(owner)}"
        cookie["vr_owner"]["path"] = "/"
        cookie["vr_owner"]["max-age"] = 365 * 24 * 60 * 60
        cookie["vr_owner"]["httponly"] = True
        cookie["vr_owner"]["samesite"] = "Lax"
        if self.secure:
            cookie["vr_owner"]["secure"] = True
        return owner, cookie["vr_owner"].OutputString()
