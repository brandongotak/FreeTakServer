"""which system users may sign in to the web UI, and which are disabled

System users are also device accounts (ATAK enrollment, certificate
packages); only those marked admin can sign in to the UI. A disabled user
can't sign in to the UI, can't enroll, and their certificates are refused on
the SSL CoT and HTTPS TAK API ports. The lists live in
<persistence>/ui_access.json; admins start out as just the built-in "admin"
user. Reads are cached briefly because the CoT services check on every
message."""
import hmac
import json
import os
import time
from typing import List, Optional

from sqlalchemy import create_engine, text

from FreeTAKServer.core.configuration.DatabaseConfiguration import DatabaseConfiguration
from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()

SETTINGS_FILE = os.path.join(str(config.persistencePath), "ui_access.json")
DEFAULT_ADMINS = ["admin"]
CACHE_SECONDS = 5

_engine = None
_cache = {"loaded": 0.0, "value": None}


def _db():
    global _engine
    if _engine is None:
        _engine = create_engine(DatabaseConfiguration().DataBaseConnectionString)
    return _engine


def _load(force: bool = False) -> dict:
    if force or time.monotonic() - _cache["loaded"] > CACHE_SECONDS:
        try:
            with open(SETTINGS_FILE) as settings_file:
                value = json.load(settings_file)
        except (OSError, ValueError):
            value = {}
        value.setdefault("admins", list(DEFAULT_ADMINS))
        value.setdefault("disabled", [])
        _cache.update(loaded=time.monotonic(), value=value)
    return _cache["value"]


def _save(settings: dict) -> None:
    temporary_file = SETTINGS_FILE + ".tmp"
    with open(temporary_file, "w") as settings_file:
        json.dump({"admins": sorted(settings["admins"]), "disabled": sorted(settings["disabled"])}, settings_file)
    os.replace(temporary_file, SETTINGS_FILE)
    _cache.update(loaded=time.monotonic(), value=settings)


def admins() -> List[str]:
    return list(_load()["admins"])


def is_admin(username: Optional[str]) -> bool:
    return bool(username) and username in _load()["admins"]


def is_disabled(username: Optional[str]) -> bool:
    return bool(username) and username in _load()["disabled"]


def _enabled_admins(settings: dict) -> List[str]:
    return [name for name in settings["admins"] if name not in settings["disabled"]]


def set_admin(username: str, admin: bool) -> None:
    """grant or revoke UI access; the last enabled admin can't be removed, so the UI can't lock everyone out"""
    settings = _load(force=True)
    if admin and username not in settings["admins"]:
        settings["admins"].append(username)
    elif not admin and username in settings["admins"]:
        if _enabled_admins(settings) == [username]:
            raise ValueError("at least one enabled user must keep web UI access")
        settings["admins"].remove(username)
    _save(settings)


def set_disabled(username: str, disabled: bool) -> None:
    """disable or re-enable a user; the last enabled admin can't be disabled"""
    settings = _load(force=True)
    if disabled and username not in settings["disabled"]:
        if _enabled_admins(settings) == [username]:
            raise ValueError("the last enabled web UI admin can't be disabled")
        settings["disabled"].append(username)
    elif not disabled and username in settings["disabled"]:
        settings["disabled"].remove(username)
    _save(settings)


def authenticate(username: str, password: str) -> Optional[str]:
    """the uid of the system user if the credentials are valid, else None"""
    if not username or not password:
        return None
    with _db().connect() as connection:
        rows = connection.execute(
            text('SELECT uid, password FROM "SystemUser" WHERE name = :name'), {"name": username}
        ).all()
    for uid, stored in rows:
        # passwords are stored as plain text by the system user API
        if stored and hmac.compare_digest(str(stored).encode(), password.encode()):
            return uid
    return None
