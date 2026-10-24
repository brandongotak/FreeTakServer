from typing import TYPE_CHECKING

from flask.json import jsonify

if TYPE_CHECKING:
    from FreeTAKServer.core.enterprise_sync.persistence.sqlalchemy.enterprise_sync_keyword import EnterpriseSyncKeyword
    from FreeTAKServer.core.enterprise_sync.persistence.sqlalchemy.enterprise_sync_data_object import EnterpriseSyncDataObject

import json
from flask import Blueprint, request
from pathlib import PurePath, Path
import hashlib
from zipfile import ZipFile
from defusedxml import ElementTree as etree
import uuid
from lxml.etree import SubElement, Element  # pylint: disable=no-name-in-module

from ..controllers.authentication import auth

from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.services.rest_api_service.controllers.rest_api_communication_controller import RestAPICommunicationController

config = MainConfig.instance()
page = Blueprint('mission', __name__)

@page.route("/MissionTable", methods=['GET'])
@auth.login_required()
def get_mission_table():
    try:
        out_data =  RestAPICommunicationController().make_request("GetMissions", "mission", {}, None, True).get_value("missions"), 200
        print(out_data)
        return out_data
    except Exception as e:
        logger.error(str(e))
        return {"message":"An error occurred accessing mission details."}, 500

@page.route("/MissionTable", methods=['DELETE'])
@auth.login_required()
def delete_missions():
    """delete missions by name as an administrator, regardless of who created them;
    expects {"missions": ["name", ...]} and notifies clients of each deletion
    """
    deleted, failed = [], []
    for name in (request.get_json(silent=True) or {}).get("missions", []):
        response = RestAPICommunicationController().make_request("DeleteMission", "mission", {"mission_id": str(name), "creatorUid": "", "force": True}, None, True)
        status = response.get_value("status")
        if status != 200:
            # 404 unknown mission; None means the mission component raised (see its DEBUG.log)
            failed.append({"name": name, "status": status})
            continue
        RestAPICommunicationController().make_request("MissionDeletedNotification", "mission", {"mission_name": response.get_value("mission_name"), "mission_tool": response.get_value("mission_tool"), "mission_creator": response.get_value("mission_creator")}, None, synchronous=False)
        deleted.append(name)
    return {"deleted": deleted, "failed": failed}, 200 if not failed else 207
