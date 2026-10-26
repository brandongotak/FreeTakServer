"""remember services switched off on the UI's configure page

FTS used to start every service on every start, so a service switched off
in the UI came back after a restart or update. The configure page's on/off
choices for the CoT and data package services are kept in
<persistence>/service_state.json and applied at startup."""
import json
import os
from typing import Dict, Optional

from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()

STATE_FILE = os.path.join(str(config.persistencePath), "service_state.json")
SERVICES = ("tcp_cot", "ssl_cot", "tcp_data_package", "ssl_data_package")


def _load() -> Dict[str, str]:
    try:
        with open(STATE_FILE) as state_file:
            return json.load(state_file)
    except (OSError, ValueError):
        return {}


def saved_status(service: str) -> Optional[str]:
    """"start", "stop", or None when never changed from the UI"""
    return _load().get(service)


def save_status(service: str, status: str) -> None:
    if service not in SERVICES or status not in ("start", "stop"):
        return
    state = _load()
    state[service] = status
    temporary_file = STATE_FILE + ".tmp"
    with open(temporary_file, "w") as state_file:
        json.dump(state, state_file)
    os.replace(temporary_file, STATE_FILE)
