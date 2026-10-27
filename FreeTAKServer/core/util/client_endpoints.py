"""the reply to /Marti/api/clientEndPoints: the devices connected right now

TAKX polls this every 30 seconds. FTS keeps connected clients in the User
table (added on connect, removed on disconnect, cleared at startup) with the
contact CoT they connected with."""
import datetime
from typing import List

from sqlalchemy import text

from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.core.util import groups
from FreeTAKServer.core.util.sync_search import timestamp

config = MainConfig.instance()


def endpoints() -> List[dict]:
    with groups._db().connect() as connection:
        rows = connection.execute(text(
            'SELECT "User".uid, "User".callsign, "Event".start FROM "User" '
            'LEFT JOIN "Event" ON "Event".uid = "User"."CoT_id"')).all()
    now = timestamp(datetime.datetime.utcnow())
    return [{
        "callsign": callsign or "",
        "uid": uid,
        # FTS doesn't record which account a streaming client belongs to
        "username": "",
        # the contact CoT's start, which is when the client connected
        "lastEventTime": start or now,
        "lastStatus": "Connected",
    } for uid, callsign, start in rows if uid]


def response() -> dict:
    return {
        "version": "3",
        "type": "com.bbn.marti.remote.ClientEndpoint",
        "data": endpoints(),
        "nodeId": config.nodeID,
    }
