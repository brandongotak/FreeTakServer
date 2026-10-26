"""enrollment codes for quick-connect QR codes

A code stands in for a user's password, but only for certificate enrollment
(the enrollment service on port 8446), and only until it expires; a
one-device code stops working shortly after its first use (one enrollment
makes several requests). Only a hash of each code is kept, in
<persistence>/enroll_codes.json."""
import base64
import datetime
import hashlib
import hmac
import io
import json
import os
import secrets
import uuid
from typing import Dict, List, Optional
from urllib.parse import quote

import qrcode

from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()

CODES_FILE = os.path.join(str(config.persistencePath), "enroll_codes.json")
# a one-device code keeps working this long after first use: one enrollment
# is several requests
GRACE = datetime.timedelta(minutes=10)
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _load() -> List[Dict]:
    try:
        with open(CODES_FILE) as codes_file:
            return json.load(codes_file)
    except (OSError, ValueError):
        return []


def _save(codes: List[Dict]) -> None:
    temporary_file = CODES_FILE + ".tmp"
    with open(temporary_file, "w") as codes_file:
        json.dump(codes, codes_file)
    os.replace(temporary_file, CODES_FILE)


def _live(code: Dict, now: datetime.datetime) -> bool:
    if now >= datetime.datetime.fromisoformat(code["expires"]):
        return False
    if code.get("one_device") and code.get("used"):
        return now < datetime.datetime.fromisoformat(code["used"]) + GRACE
    return True


def create(username: str, hours: int = 24, one_device: bool = True, created_by: str = "") -> Dict:
    """make a code for username; the plain code is only returned here"""
    hours = max(1, min(int(hours), 24 * 30))
    code = "".join(secrets.choice(ALPHABET) for _ in range(20))
    now = _now()
    record = {"id": str(uuid.uuid4()), "username": username, "hash": _hash(code),
              "expires": (now + datetime.timedelta(hours=hours)).isoformat(timespec="seconds"),
              "one_device": bool(one_device), "used": None,
              "created": now.isoformat(timespec="seconds"), "created_by": created_by}
    # drop codes that can no longer be used while saving
    codes = [c for c in _load() if _live(c, now)] + [record]
    _save(codes)
    return dict(record, code=code)


def verify(username: str, code: str) -> bool:
    """whether code is a usable enrollment code for username; marks it used"""
    if not username or not code:
        return False
    now, digest = _now(), _hash(code)
    codes = _load()
    for record in codes:
        if record["username"] == username and hmac.compare_digest(record["hash"], digest) and _live(record, now):
            if not record.get("used"):
                record["used"] = now.isoformat(timespec="seconds")
                _save(codes)
            return True
    return False


def active() -> List[Dict]:
    """codes that still work, without their hashes"""
    now = _now()
    return [{key: value for key, value in c.items() if key != "hash"} for c in _load() if _live(c, now)]


def revoke(code_id: str) -> None:
    _save([c for c in _load() if c["id"] != code_id])


def atak_link(host: str, username: str, code: str) -> str:
    """ATAK quick-connect link: ATAK enrolls on the enrollment port with the code as password"""
    return (f"tak://com.atakmap.app/enroll?host={quote(host, safe='')}"
            f"&username={quote(username, safe='')}&token={quote(code, safe='')}")


def itak_registration(host: str, ssl_port: int, username: str, code: str, registration_id: str) -> str:
    """iTAK registration QR payload: it enrolls with these credentials, then connects"""
    return json.dumps({"passphrase": "false", "type": "registration",
                       "serverCredentials": {"connectionString": f"{host}:{ssl_port}:ssl"},
                       "userCredentials": {"username": username, "password": code, "registrationId": registration_id}})


def qr_png_data_uri(payload: str) -> str:
    image = qrcode.make(payload, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
