import json
from flask import Blueprint, request, make_response
from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.core.util import groups
from FreeTAKServer.core.util import device_profile

config = MainConfig.instance()
page = Blueprint('misc', __name__)

@page.route("/Marti/api/groups/groupCacheEnabled")
def group_cache():
    # ATAK offers its channels screen when the server keeps per-user active
    # groups, which FTS does while group routing is on
    return {
        "version": "3",
        "type": "java.lang.Boolean",
        "data": groups.groups_enabled(),
        "nodeId": config.nodeID
    }

@page.route('/Marti/api/device/profile/connection', methods=["GET"])
def connection_profile():
    """the device profile clients fetch on connect (deviceProfileEnableOnConnect);
    it keeps channels enabled for this server"""
    from flask import Response
    package = device_profile.profile_package(device_profile.request_host(request.host), "Connection")
    return Response(package, 200, mimetype="application/zip",
                    headers={"Content-Disposition": "attachment; filename=profile.zip"})

@page.route('/Marti/api/clientEndPoints', methods=["GET"])
def clientEndPoint():
    return {
        "version": "3",
        "type": "com.bbn.marti.remote.ClientEndpoint",
        "data": [
            {
                "callsign": "DOWN",
                "uid": "ANDROID-199eeda473669973",
                "username": "ghost",
                "lastEventTime": "2023-06-16T15:20:55.871Z",
                "lastStatus": "Connected"
            }
        ],
        "nodeId": config.nodeID
    }