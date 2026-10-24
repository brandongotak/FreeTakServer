"""the chat message sent to clients when they connect, editable at runtime from the UI

the CoT services run in separate processes, so the setting is kept in a small
JSON file under the persistence path that they read on every connection; until
that file exists the FTS_CONNECTION_MESSAGE setting is used"""
import json
import os
from typing import Dict, Optional
from xml.sax.saxutils import escape

from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()

SETTINGS_FILE = os.path.join(str(config.persistencePath), "connection_message.json")
MAX_LENGTH = 1000


def get_settings() -> Dict[str, object]:
    """return {"enabled": bool, "message": str}"""
    try:
        with open(SETTINGS_FILE) as settings_file:
            data = json.load(settings_file)
        return {"enabled": bool(data.get("enabled", True)), "message": str(data.get("message", ""))}
    except (OSError, ValueError):
        message = config.ConnectionMessage
        # the config value is typed as a string, so a YAML null arrives as "None"
        enabled = message not in (None, "", "None")
        return {"enabled": enabled, "message": message if enabled else ""}


def save_settings(enabled: bool, message: str) -> Dict[str, object]:
    """persist the setting atomically so a connecting client never reads a partial file"""
    temporary_file = SETTINGS_FILE + ".tmp"
    with open(temporary_file, "w") as settings_file:
        json.dump({"enabled": bool(enabled), "message": str(message)[:MAX_LENGTH]}, settings_file)
    os.replace(temporary_file, SETTINGS_FILE)
    return get_settings()


def get_connection_remarks() -> Optional[str]:
    """the XML-escaped message to send to a connecting client, or None when disabled or empty"""
    settings = get_settings()
    if not settings["enabled"] or not str(settings["message"]).strip():
        return None
    return escape(str(settings["message"]))
