"""video feeds offered to devices, and the MediaMTX streams they can come from

The REST API service (UI, Node-RED's VideoChecker) and the HTTPS TAK API service
(ATAK's Videos > download) run in separate processes, so the feeds are kept in a
small JSON file under the persistence path.

A feed is either a URL (an IP camera, a drone, anything a device can play),
added in the UI or uploaded by a device, or a MediaMTX stream path, whose URL is
built with the host the device reached FTS on."""
import datetime as dt
import json
import os
import re
import uuid
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import quoteattr

import defusedxml.ElementTree as ET
import requests

from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()

SETTINGS_FILE = os.path.join(str(config.persistencePath), "video_feeds.json")

PROTOCOLS = ("rtsp", "rtsps", "rtmp", "rtmps", "srt", "udp", "rtp", "http", "https")
DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322, "rtmp": 1935, "rtmps": 443, "http": 80, "https": 443}
STREAM_NAME = re.compile(r"^[A-Za-z0-9._~/-]{1,100}$")
MAX_ALIAS = 100

# MediaMTX path source types a publisher can be kicked from, and the API collection for each
KICKABLE_SOURCES = {
    "rtspSession": "rtsp/sessions",
    "rtspsSession": "rtsps/sessions",
    "rtmpConn": "rtmp/conns",
    "rtmpsConn": "rtmps/conns",
    "srtConn": "srt/conns",
    "webRTCSession": "webrtc/sessions",
    "moqSession": "moq/sessions",
}


class VideoError(ValueError):
    """a problem to show in the UI"""


## the feed list

def _load() -> dict:
    try:
        with open(SETTINGS_FILE) as settings_file:
            data = json.load(settings_file)
    except (OSError, ValueError):
        data = {}
    return {"feeds": [feed for feed in data.get("feeds", []) if isinstance(feed, dict) and feed.get("uid")],
            "autoAdd": bool(data.get("autoAdd", True)),
            "ignored": [str(name) for name in data.get("ignored", [])]}


def _save(data: dict) -> None:
    """write atomically so the other service never reads a partial file"""
    temporary_file = SETTINGS_FILE + ".tmp"
    with open(temporary_file, "w") as settings_file:
        json.dump(data, settings_file, indent=1)
    os.replace(temporary_file, SETTINGS_FILE)


def feeds() -> List[dict]:
    return _load()["feeds"]


def get(uid: str) -> Optional[dict]:
    return next((feed for feed in feeds() if feed["uid"] == uid), None)


def settings() -> Dict[str, object]:
    data = _load()
    return {"autoAdd": data["autoAdd"], "ignored": data["ignored"]}


def set_auto_add(enabled: bool) -> None:
    data = _load()
    data["autoAdd"] = bool(enabled)
    _save(data)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_alias(alias: str, fallback: str) -> str:
    alias = " ".join(str(alias or "").split())[:MAX_ALIAS]
    return alias or fallback[:MAX_ALIAS]


def check_url(url: str) -> str:
    url = str(url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in PROTOCOLS or not parsed.hostname:
        raise VideoError(f"enter a URL starting with one of: {', '.join(p + '://' for p in PROTOCOLS)}")
    if parsed.scheme.lower() in ("srt", "udp", "rtp") and not parsed.port:
        raise VideoError(f"{parsed.scheme} URLs need a port")
    return url


def check_stream_name(name: str) -> str:
    name = str(name or "").strip().strip("/")
    if not STREAM_NAME.match(name):
        raise VideoError("stream names may only use letters, digits and . _ ~ / -")
    return name


def add_url(alias: str, url: str, source: str = "manual", uid: Optional[str] = None) -> Tuple[dict, bool]:
    """add a feed for a URL; returns (feed, created), an existing feed when the URL is already listed"""
    url = check_url(url)
    data = _load()
    for feed in data["feeds"]:
        if feed.get("url") == url:
            return feed, False
    feed = {"uid": uid or str(uuid.uuid4()), "alias": _clean_alias(alias, urlparse(url).path.strip("/") or url),
            "url": url, "source": source, "created": _now()}
    data["feeds"].append(feed)
    _save(data)
    return feed, True


def add_stream(name: str, alias: str = "", source: str = "mediamtx") -> Tuple[dict, bool]:
    """add a feed for a MediaMTX stream; adding it by hand also stops ignoring it"""
    name = check_stream_name(name)
    data = _load()
    data["ignored"] = [ignored for ignored in data["ignored"] if ignored != name]
    for feed in data["feeds"]:
        if feed.get("path") == name:
            _save(data)
            return feed, False
    feed = {"uid": str(uuid.uuid4()), "alias": _clean_alias(alias, name), "path": name,
            "source": source, "created": _now()}
    data["feeds"].append(feed)
    _save(data)
    return feed, True


def auto_add_stream(name: str) -> Tuple[Optional[dict], bool]:
    """called for each live MediaMTX stream (Node-RED's VideoChecker); returns (feed, created),
    or (None, False) when automatic feeds are off or the stream's feed was deleted"""
    name = check_stream_name(name)
    data = _load()
    if not data["autoAdd"] or name in data["ignored"]:
        return None, False
    for feed in data["feeds"]:
        if feed.get("path") == name:
            return feed, False
    return add_stream(name)


def remove(uid: str) -> Optional[dict]:
    """delete a feed; a MediaMTX stream's feed isn't added again automatically"""
    data = _load()
    feed = next((feed for feed in data["feeds"] if feed["uid"] == uid), None)
    if feed is None:
        return None
    data["feeds"] = [other for other in data["feeds"] if other["uid"] != uid]
    if feed.get("path") and feed["path"] not in data["ignored"]:
        data["ignored"].append(feed["path"])
    _save(data)
    return feed


## addresses

def public_host() -> str:
    """the name devices use for this server: the enrollment domain, else the configured address"""
    from FreeTAKServer.core.util import enrollment_setup
    return enrollment_setup.status().get("domain") or str(config.UserConnectionIP)


def stream_url(name: str, host: Optional[str] = None) -> str:
    return f"rtsp://{host or public_host()}:{int(config.VideoServerRTSPPort)}/{quote(name)}"


def feed_url(feed: dict, host: Optional[str] = None) -> str:
    return stream_url(feed["path"], host) if feed.get("path") else feed["url"]


def _url_parts(url: str) -> Dict[str, str]:
    """split a URL into the fields of ATAK's connection entry"""
    parsed = urlparse(url)
    protocol = parsed.scheme.lower()
    address = parsed.hostname or ""
    if parsed.username:
        # the user info stays part of the address, which is how ATAK keeps it too
        address = parsed.netloc.rsplit("@", 1)[0] + "@" + address
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    return {"protocol": protocol, "address": address, "port": str(parsed.port or DEFAULT_PORTS.get(protocol, -1)),
            "path": path}


## what devices download

def vcm_xml(host: Optional[str] = None) -> bytes:
    """GET /Marti/vcm: the feed list ATAK's Videos tool downloads"""
    connections = Element("videoConnections")
    for feed in feeds():
        parts = _url_parts(feed_url(feed, host))
        entry = SubElement(connections, "feed")
        for tag, value in (("protocol", parts["protocol"]), ("alias", feed["alias"]), ("uid", feed["uid"]),
                           ("address", parts["address"]), ("port", parts["port"]), ("roverPort", "-1"),
                           ("ignoreEmbeddedKLV", "false"), ("preferredMacAddress", ""),
                           ("preferredInterfaceAddress", ""), ("path", parts["path"]), ("buffer", "-1"),
                           ("timeout", "5000"), ("rtspReliable", "1")):
            SubElement(entry, tag).text = value
    return tostring(connections)


def video_collections(host: Optional[str] = None) -> dict:
    """GET /Marti/api/video: the same list in the newer JSON form"""
    collections = []
    for feed in feeds():
        url = feed_url(feed, host)
        collections.append({
            "uuid": feed["uid"], "active": True, "alias": feed["alias"], "thumbnail": "", "classification": "",
            "feeds": [{"uuid": feed["uid"], "active": True, "alias": feed["alias"], "url": url, "order": 0,
                       "macAddress": "", "roverPort": "-1", "ignoreEmbeddedKLV": "false", "source": url,
                       "networkTimeout": "5000", "bufferTime": "-1", "rtspReliable": "1", "thumbnail": "",
                       "classification": "", "latitude": "", "longitude": "", "fov": "", "heading": "",
                       "range": "", "width": 0, "height": 0, "bitrate": 0}],
        })
    return {"videoConnections": collections}


def _own_stream(url: str) -> Optional[str]:
    """the stream name when a URL points at this server's video server"""
    parsed = urlparse(url)
    own_hosts = {public_host(), str(config.UserConnectionIP), str(config.ip), "127.0.0.1", "localhost"}
    if (parsed.scheme.lower() == "rtsp" and parsed.port == int(config.VideoServerRTSPPort)
            and parsed.hostname in own_hosts and not parsed.query):
        return parsed.path.strip("/") or None
    return None


def _add_device_feed(alias: str, url: str, uid: Optional[str]) -> bool:
    """add a feed a device shared; one pointing at this server's video server becomes a stream feed"""
    try:
        stream = _own_stream(check_url(url))
        if stream:
            return add_stream(stream, alias, source="device")[1]
        return add_url(alias, url, source="device", uid=uid)[1]
    except VideoError:
        return False


def add_from_vcm_xml(body: bytes) -> int:
    """POST /Marti/vcm: feeds a device shares with the server; returns how many were new"""
    added = 0
    for entry in ET.fromstring(body).iter("feed"):
        values = {child.tag: (child.text or "").strip() for child in entry}
        protocol = values.get("protocol", "").lower()
        port = values.get("port", "")
        path = values.get("path", "")
        if path and not path.startswith(("/", "?")):
            path = "/" + path
        url = f"{protocol}://{values.get('address', '')}{':' + port if port not in ('', '-1') else ''}{path}"
        added += _add_device_feed(values.get("alias", ""), url, values.get("uid") or None)
    return added


def add_from_collections(body: dict) -> int:
    """POST /Marti/api/video: the JSON form of a device's shared feeds"""
    added = 0
    for collection in (body or {}).get("videoConnections", []):
        for entry in collection.get("feeds", []):
            added += _add_device_feed(entry.get("alias") or collection.get("alias", ""), entry.get("url", ""),
                                      entry.get("uuid") or collection.get("uuid") or None)
    return added


## what's sent to connected devices

def _cot_times(stale_hours: int) -> Tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    stamp = "%Y-%m-%dT%H:%M:%S.%fZ"
    return now.strftime(stamp), (now + dt.timedelta(hours=stale_hours)).strftime(stamp)


def announce_cot(feed: dict) -> str:
    """the CoT that adds a feed to every connected device's video list"""
    url = feed_url(feed)
    parts = _url_parts(url)
    now, stale = _cot_times(24)
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<event version="2.0" uid={quoteattr(feed["uid"])} type="b-i-v" how="m-g" time="{now}" start="{now}" stale="{stale}">'
            f'<point lat="0" lon="0" hae="0" ce="9999999" le="9999999"/>'
            f'<detail><__video url={quoteattr(url)}>'
            f'<ConnectionEntry networkTimeout="5000" uid={quoteattr(feed["uid"])} path={quoteattr(parts["path"])} '
            f'protocol={quoteattr(parts["protocol"])} bufferTime="-1" address={quoteattr(parts["address"])} '
            f'port={quoteattr(parts["port"])} roverPort="-1" rtspReliable="1" ignoreEmbeddedKLV="false" '
            f'alias={quoteattr(feed["alias"])}/></__video>'
            f'<contact callsign={quoteattr(feed["alias"])}/></detail></event>')


def delete_cot(uid: str) -> str:
    """the CoT that removes a feed from connected devices"""
    now, stale = _cot_times(1)
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<event version="2.0" uid="{uuid.uuid4()}" type="t-x-d-d" how="h-g-i-g-o" time="{now}" start="{now}" stale="{stale}">'
            f'<point lat="0" lon="0" hae="0" ce="9999999" le="9999999"/>'
            f'<detail><link uid={quoteattr(uid)} relation="p-p" type="b-i-v"/><__forcedelete/></detail></event>')


## MediaMTX

def _api(path: str, method: str = "GET"):
    try:
        reply = requests.request(method, str(config.VideoServerAPI).rstrip("/") + path, timeout=5)
    except requests.RequestException as e:
        raise VideoError(f"the video server (MediaMTX) is not reachable: {e}") from e
    if reply.status_code == 404:
        raise VideoError("stream not found")
    if reply.status_code >= 400:
        raise VideoError(f"the video server answered {reply.status_code}: {reply.text[:200]}")
    return reply.json() if reply.content else {}


def live_streams() -> List[dict]:
    """the paths MediaMTX has, with their publisher and viewers"""
    items, page = [], 0
    while True:
        reply = _api(f"/v3/paths/list?page={page}&itemsPerPage=100")
        items += reply.get("items", [])
        page += 1
        if page >= int(reply.get("pageCount", 1) or 1):
            break
    listed = {feed.get("path"): feed["uid"] for feed in feeds() if feed.get("path")}
    streams = []
    for item in items:
        source = item.get("source") or {}
        tracks = item.get("tracks2") or [{"codec": codec} for codec in item.get("tracks") or []]
        streams.append({
            "name": item.get("name", ""),
            "online": bool(item.get("online", item.get("ready", False))),
            "since": item.get("onlineTime") or item.get("readyTime"),
            "sourceType": source.get("type", ""),
            "kickable": source.get("type") in KICKABLE_SOURCES,
            "readers": len(item.get("readers") or []),
            "inboundBytes": item.get("inboundBytes", item.get("bytesReceived", 0)),
            "tracks": [track.get("codec", "") for track in tracks],
            "url": stream_url(item.get("name", "")),
            "feedUid": listed.get(item.get("name")),
        })
    return sorted(streams, key=lambda stream: stream["name"])


def kick_publisher(name: str) -> None:
    """disconnect whoever is publishing a stream; they may reconnect"""
    name = check_stream_name(name)
    source = (_api(f"/v3/paths/get/{quote(name)}").get("source") or {})
    collection = KICKABLE_SOURCES.get(source.get("type"))
    if not collection:
        raise VideoError("this stream has no publisher to disconnect" if not source else
                         f"MediaMTX pulls this stream itself ({source.get('type')}); change its config instead")
    _api(f"/v3/{collection}/kick/{quote(str(source.get('id', '')))}", method="POST")
