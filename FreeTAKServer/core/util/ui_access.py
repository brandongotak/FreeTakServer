"""which system users may sign in to the web UI

System users are also device accounts (ATAK enrollment, certificate
packages); only those marked admin can sign in to the UI. The list lives in
<persistence>/ui_access.json and starts out as just the built-in "admin"
user."""
import hmac
import json
import os
from typing import List, Optional

from sqlalchemy import create_engine, text

from FreeTAKServer.core.configuration.DatabaseConfiguration import DatabaseConfiguration
from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()

SETTINGS_FILE = os.path.join(str(config.persistencePath), "ui_access.json")
DEFAULT_ADMINS = ["admin"]

_engine = None


def _db():
    global _engine
    if _engine is None:
        _engine = create_engine(DatabaseConfiguration().DataBaseConnectionString)
    return _engine


def admins() -> List[str]:
    try:
        with open(SETTINGS_FILE) as settings_file:
            return list(json.load(settings_file).get("admins", DEFAULT_ADMINS))
    except (OSError, ValueError):
        return list(DEFAULT_ADMINS)


def is_admin(username: Optional[str]) -> bool:
    return bool(username) and username in admins()


def set_admin(username: str, admin: bool) -> None:
    """grant or revoke UI access; the last admin can't be removed, so the UI can't lock everyone out"""
    current = admins()
    if admin and username not in current:
        current.append(username)
    elif not admin and username in current:
        if len(current) == 1:
            raise ValueError("at least one user must keep web UI access")
        current.remove(username)
    temporary_file = SETTINGS_FILE + ".tmp"
    with open(temporary_file, "w") as settings_file:
        json.dump({"admins": sorted(current)}, settings_file)
    os.replace(temporary_file, SETTINGS_FILE)


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
