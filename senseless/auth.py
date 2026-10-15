"""Sense cloud authentication.

Credentials: SENSE_EMAIL / SENSE_PASSWORD env vars, else the Bitwarden item
named by SENSE_BW_ITEM (default "sense.com") using ~/.bw_session.
The access token is cached (0600) so restarts don't re-authenticate.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

API = "https://api.sense.com/apiservice/api/v1"
# Mirrors the Android client (com.sense.androidclient 2026.14-10839).
HEADERS = {
    "Sense-Client-Version": "2026.14-10839",
    "X-Sense-Protocol": "13",
    "X-Sense-Client-Type": "android",
}
# Docker image sets this to /data/token.json so it lives on the persistent volume.
TOKEN_CACHE = Path(os.environ.get("SENSELESS_TOKEN_CACHE", "~/.config/senseless/token.json")).expanduser()


class AuthError(RuntimeError):
    pass


@dataclass
class Session:
    access_token: str
    user_id: int
    monitor_id: int

    def headers(self) -> dict:
        return {**HEADERS, "Authorization": f"bearer {self.access_token}"}


def _credentials() -> tuple[str, str]:
    email, pw = os.environ.get("SENSE_EMAIL"), os.environ.get("SENSE_PASSWORD")
    if email and pw:
        return email, pw
    bw_session = Path("~/.bw_session").expanduser().read_text().strip()
    item = os.environ.get("SENSE_BW_ITEM", "sense.com")
    out = subprocess.check_output(
        ["bw", "get", "item", item, "--session", bw_session], stderr=subprocess.DEVNULL
    )
    login = json.loads(out)["login"]
    return login["username"], login["password"]


def login() -> Session:
    email, pw = _credentials()
    r = requests.post(f"{API}/authenticate", data={"email": email, "password": pw}, headers=HEADERS, timeout=20)
    if r.status_code in (401, 403):
        raise AuthError(f"credentials rejected: HTTP {r.status_code}")
    if r.status_code != 200:
        raise AuthError(f"authenticate failed: HTTP {r.status_code}")
    a = r.json()
    if not a.get("authorized", True) or "access_token" not in a:
        # MFA accounts get an mfa_token here instead; not supported yet.
        raise AuthError("authenticate returned no access_token (MFA enabled?)")
    s = Session(a["access_token"], a["user_id"], a["monitors"][0]["id"])
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_CACHE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(s.__dict__))
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_CACHE)
    return s


def session(fresh: bool = False) -> Session:
    if not fresh and TOKEN_CACHE.exists():
        try:
            return Session(**json.loads(TOKEN_CACHE.read_text()))
        except (ValueError, TypeError):
            pass
    return login()
