"""the devices connected right now, for the web UI's Devices page

FTS keeps connected clients in the User table (added on connect, removed on
disconnect, cleared at startup) with the contact CoT they connected with. The
CoT services run in their own processes, so each also records what it has
heard from its devices since (last message, position, battery) in a JSON file
under the persistence path, written at most every few seconds; the REST API
merges the two.

Disconnecting goes the other way, through disconnect.json: the service holding
the device drops it on its next message (ATAK and iTAK send one at least every
15 seconds or so). A request only applies to the connection it was made for,
so a device that reconnects isn't dropped again."""
import datetime
import json
import os
import re
import time
from typing import Dict, List, Optional

from sqlalchemy import text

from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.core.util import groups

config = MainConfig.instance()

ACTIVITY_FILE = os.path.join(str(config.persistencePath), "devices-{service}.json")
DISCONNECT_FILE = os.path.join(str(config.persistencePath), "disconnect.json")
WRITE_SECONDS = 5
DISCONNECT_CACHE_SECONDS = 2
# a request for a device that never sends again is forgotten after this
DISCONNECT_KEEP_SECONDS = 600

EVENT = re.compile(rb"<event\b[^>]*>")
POINT = re.compile(rb"<point\b[^>]*>")
STATUS = re.compile(rb"<status\b[^>]*>")

# in each CoT service: uid -> activity, and when the file was last written
_activity: Dict[str, dict] = {}
_written = {"at": 0.0}
_disconnect_cache = {"loaded": 0.0, "value": {}}


def _now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _attribute(tag: bytes, name: bytes) -> Optional[str]:
    match = re.search(rb"\b" + name + rb'="([^"]*)"', tag)
    return match.group(1).decode("utf-8", "replace") if match else None


def _read_json(path: str, default):
    try:
        with open(path) as json_file:
            return json.load(json_file)
    except (OSError, ValueError):
        return default


def _write_json(path: str, value) -> None:
    temporary_file = path + ".tmp"
    with open(temporary_file, "w") as json_file:
        json.dump(value, json_file)
    os.replace(temporary_file, path)


def _save_activity(service: str, force: bool = False) -> None:
    if not force and time.monotonic() - _written["at"] < WRITE_SECONDS:
        return
    _written["at"] = time.monotonic()
    # the page is only informational; never let it get in the way of routing
    try:
        _write_json(ACTIVITY_FILE.format(service=service), _activity)
    except (OSError, TypeError, ValueError):
        pass


# called by the CoT services

def connected(service: str, uid: str) -> None:
    _activity[uid] = {"service": service, "connected": _now(), "connectedAt": time.time(),
                      "lastSeen": _now(), "messages": 0}
    _save_activity(service, force=True)


def heard(service: str, uid: str, xml: bytes) -> None:
    """note a message from a device; its own position reports update where it is"""
    if isinstance(xml, str):
        xml = xml.encode("utf-8", "replace")
    entry = _activity.get(uid)
    if entry is None:
        # connected before this service started recording
        entry = _activity[uid] = {"service": service, "connected": None, "connectedAt": 0.0, "messages": 0}
    entry["lastSeen"] = _now()
    entry["messages"] += 1
    event = EVENT.search(xml[:512])
    if event and _attribute(event.group(0), b"uid") == uid and (_attribute(event.group(0), b"type") or "").startswith("a-"):
        point = POINT.search(xml)
        if point:
            try:
                lat, lon = float(_attribute(point.group(0), b"lat")), float(_attribute(point.group(0), b"lon"))
            except (TypeError, ValueError):
                lat = lon = 0.0
            # devices without a fix report 0, 0
            if lat or lon:
                entry.update(lat=lat, lon=lon, positionTime=_now())
        status = STATUS.search(xml)
        battery = _attribute(status.group(0), b"battery") if status else None
        if battery:
            entry["battery"] = battery
    _save_activity(service)


def gone(service: str, uid: str) -> None:
    if _activity.pop(uid, None) is not None:
        _save_activity(service, force=True)


def should_disconnect(uid: str) -> bool:
    """whether an admin asked to disconnect this connection of the device"""
    if time.monotonic() - _disconnect_cache["loaded"] > DISCONNECT_CACHE_SECONDS:
        _disconnect_cache.update(loaded=time.monotonic(), value=_read_json(DISCONNECT_FILE, {}))
    requested = _disconnect_cache["value"].get(uid)
    entry = _activity.get(uid)
    return requested is not None and entry is not None and requested > entry["connectedAt"]


# called by the REST API

def request_disconnect(uid: str) -> None:
    requests = _read_json(DISCONNECT_FILE, {})
    now = time.time()
    requests = {key: at for key, at in requests.items() if now - at < DISCONNECT_KEEP_SECONDS}
    requests[uid] = now
    _write_json(DISCONNECT_FILE, requests)


def _activity_files() -> Dict[str, dict]:
    merged = {}
    for service in ("ssl", "tcp"):
        merged.update(_read_json(ACTIVITY_FILE.format(service=service), {}))
    return merged


def devices() -> List[dict]:
    with groups._db().connect() as connection:
        rows = connection.execute(text(
            'SELECT u.uid, u.callsign, u."CN", u."IP", e.start, g.name, g.role, '
            't.device, t.platform, t.os, t.version, s.battery, p.lat, p.lon '
            'FROM "User" u LEFT JOIN "Event" e ON e.uid = u."CoT_id" '
            'LEFT JOIN "_Group" g ON g."PrimaryKey" = e.uid '
            'LEFT JOIN "Takv" t ON t."PrimaryKey" = e.uid '
            'LEFT JOIN "Status" s ON s."PrimaryKey" = e.uid '
            'LEFT JOIN "Point" p ON p."PrimaryKey" = e.uid')).all()
    activity = _activity_files()
    filtering = groups.groups_enabled()
    output = []
    for (uid, callsign, common_name, ip, start, team, role,
         device, platform, os_name, version, battery, lat, lon) in rows:
        if not uid:
            continue
        seen = activity.get(uid, {})
        try:
            username = groups.username_for_common_name(common_name)
        except Exception:
            username = None
        output.append({
            "uid": uid,
            "callsign": callsign or "",
            "team": team or "",
            "role": role or "",
            "username": username or "",
            "certificateName": common_name if common_name and common_name != "placeholder" else "",
            "groups": groups.active_groups(username) if filtering else None,
            "ip": ip or "",
            # the TCP port has no client certificates
            "service": seen.get("service") or ("ssl" if common_name is not None else "tcp"),
            "platform": " ".join(part for part in (platform, version) if part),
            "device": device or "",
            # ATAK reports the Android API level here, e.g. 34
            "os": os_name or "",
            "connected": seen.get("connected") or start or "",
            "lastSeen": seen.get("lastSeen") or "",
            "messages": seen.get("messages", 0),
            "battery": seen.get("battery") or battery or "",
            "lat": seen.get("lat", lat if lat or lon else None),
            "lon": seen.get("lon", lon if lat or lon else None),
            "positionTime": seen.get("positionTime") or "",
        })
    return sorted(output, key=lambda device: device["callsign"].lower())
