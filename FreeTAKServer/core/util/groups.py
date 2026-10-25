"""TAK groups (channels) for CoT routing

Groups are assigned per system user as a comma separated list in the user's
"group" field; a user without groups, an unidentified client (an unknown
certificate, or anyone on the plain TCP CoT port) is in __ANON__. A client
receives CoT from another client only when they share an active group.

Filtering is off until enabled. The on/off switch and each user's channel
selections (from ATAK's channels screen) are kept in a small JSON file under
the persistence path, because the CoT services, the TAK API and the REST API
run in separate processes; reads are cached for a few seconds."""
import json
import os
import re
import time
from typing import Dict, Iterable, List, Optional

from sqlalchemy import create_engine, text

from FreeTAKServer.core.configuration.DatabaseConfiguration import DatabaseConfiguration
from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()

ANONYMOUS_GROUP = "__ANON__"
SETTINGS_FILE = os.path.join(str(config.persistencePath), "groups.json")
CACHE_SECONDS = 5

_engine = None
_settings_cache = {"loaded": 0.0, "value": None}
_user_cache: Dict[str, tuple] = {}


def _db():
    global _engine
    if _engine is None:
        _engine = create_engine(DatabaseConfiguration().DataBaseConnectionString)
    return _engine


def parse_groups(field: Optional[str]) -> List[str]:
    """split a user's group field ("alpha, medics") into group names"""
    names = [name.strip() for name in (field or "").split(",")]
    unique = list(dict.fromkeys(name for name in names if name and name != "None"))
    return unique or [ANONYMOUS_GROUP]


def _load_settings(force: bool = False) -> dict:
    if force or time.monotonic() - _settings_cache["loaded"] > CACHE_SECONDS:
        try:
            with open(SETTINGS_FILE) as settings_file:
                value = json.load(settings_file)
        except (OSError, ValueError):
            value = {}
        value.setdefault("enabled", False)
        value.setdefault("inactive", {})
        _settings_cache.update(loaded=time.monotonic(), value=value)
    return _settings_cache["value"]


def _save_settings(settings: dict) -> None:
    temporary_file = SETTINGS_FILE + ".tmp"
    with open(temporary_file, "w") as settings_file:
        json.dump(settings, settings_file)
    os.replace(temporary_file, SETTINGS_FILE)
    _settings_cache.update(loaded=time.monotonic(), value=settings)


def groups_enabled() -> bool:
    return bool(_load_settings().get("enabled"))


def set_groups_enabled(enabled: bool) -> None:
    settings = _load_settings(force=True)
    settings["enabled"] = bool(enabled)
    _save_settings(settings)


def peer_common_name(sock) -> Optional[str]:
    """the CN of a TLS client certificate, if the socket has one"""
    try:
        certificate = sock.getpeercert()
    except Exception:
        return None
    for relative_name in (certificate or {}).get("subject", ()):
        for key, value in relative_name:
            if key == "commonName":
                return value
    return None


def username_for_common_name(common_name: Optional[str]) -> Optional[str]:
    """map a client certificate CN to a system user: enrollment certs use the
    username as CN, UI-generated certs the certificate package name"""
    if not common_name:
        return None
    with _db().connect() as connection:
        row = connection.execute(
            text('SELECT name FROM "SystemUser" WHERE name = :cn OR certificate_package_name = :package LIMIT 1'),
            {"cn": common_name, "package": common_name + ".zip"},
        ).first()
    return row[0] if row else None


def assigned_groups(username: Optional[str]) -> List[str]:
    """the groups a user belongs to; unidentified clients are in __ANON__"""
    if not username:
        return [ANONYMOUS_GROUP]
    cached = _user_cache.get(username)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    with _db().connect() as connection:
        fields = connection.execute(
            text('SELECT "group" FROM "SystemUser" WHERE name = :name'), {"name": username}
        ).scalars().all()
    groups = parse_groups(",".join(field for field in fields if field)) if fields else [ANONYMOUS_GROUP]
    _user_cache[username] = (time.monotonic(), groups)
    return groups


def active_groups(username: Optional[str]) -> List[str]:
    """assigned groups minus the channels the user switched off"""
    inactive = set(_load_settings().get("inactive", {}).get(username or "", []))
    return [group for group in assigned_groups(username) if group not in inactive]


def set_active_groups(username: str, states: Dict[str, bool]) -> None:
    """record channel on/off selections for a user; groups not mentioned keep their state"""
    settings = _load_settings(force=True)
    inactive = set(settings["inactive"].get(username, []))
    for group, active in states.items():
        if active:
            inactive.discard(group)
        else:
            inactive.add(group)
    settings["inactive"][username] = sorted(inactive)
    _save_settings(settings)


GROUP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 _.\-]{1,50}$")


def defined_groups() -> List[str]:
    """groups created in the UI, offered when assigning users to groups"""
    return sorted(_load_settings().get("defined", []))


def add_defined_group(name: str) -> str:
    """create a group; names can't contain commas (the user field is comma separated)"""
    name = name.strip()
    if not GROUP_NAME_PATTERN.match(name) or name == ANONYMOUS_GROUP:
        raise ValueError("group names are 1-50 letters, digits, spaces, _ . or -")
    settings = _load_settings(force=True)
    settings["defined"] = sorted(set(settings.get("defined", [])) | {name})
    _save_settings(settings)
    return name


def remove_defined_group(name: str) -> None:
    """stop offering a group; users who already have it keep it until edited"""
    settings = _load_settings(force=True)
    settings["defined"] = [group for group in settings.get("defined", []) if group != name]
    _save_settings(settings)


def all_group_names() -> List[str]:
    """every defined group and group assigned to any user, plus __ANON__, sorted"""
    with _db().connect() as connection:
        fields = connection.execute(text('SELECT "group" FROM "SystemUser"')).scalars().all()
    names = {ANONYMOUS_GROUP} | set(defined_groups())
    for field in fields:
        names.update(parse_groups(field))
    return sorted(names)


def tak_group_list(username: Optional[str]) -> List[dict]:
    """a user's groups as TAK's /Marti/api/groups/all lists them: one entry per
    direction, with a bit position that is stable across users"""
    positions = {name: position for position, name in enumerate(all_group_names())}
    active = set(active_groups(username))
    return [{"name": name, "direction": direction, "created": "2023-02-22", "type": "SYSTEM",
             "bitpos": positions.get(name, 0), "active": name in active, "description": ""}
            for name in assigned_groups(username) for direction in ("IN", "OUT")]


def apply_tak_active_groups(username: Optional[str], entries: Iterable[dict]) -> None:
    """save the channel selections from TAK's PUT /Marti/api/groups/active;
    only the user's own groups can be switched, and unidentified clients can't switch"""
    if not username:
        return
    assigned = set(assigned_groups(username))
    states: Dict[str, bool] = {}
    for entry in entries:
        name = entry.get("name")
        if name in assigned:
            # a group is on if any of its directions is on
            states[name] = states.get(name, False) or bool(entry.get("active"))
    set_active_groups(username, states)


def can_receive(source_groups: Optional[Iterable[str]], recipient_username: Optional[str]) -> bool:
    """whether a recipient may receive a message sent with source_groups;
    messages without source groups (server generated) go to everyone"""
    if source_groups is None:
        return True
    return bool(set(source_groups) & set(active_groups(recipient_username)))
