"""the replies to TAK Server's two sync search calls

/Marti/sync/search answers {"resultCount", "results"} with capitalised
fields (what ATAK reads); /Marti/api/sync/search answers TAK Server's API
envelope with the mission API Resource objects as its data (what TAKX's
Java client reads). In both, the
hash is the SHA-256 that content is downloaded by."""
import json
from typing import List

from flask import Response

from FreeTAKServer.core.configuration.MainConfig import MainConfig

config = MainConfig.instance()


def timestamp(moment) -> str:
    """UTC time with milliseconds, as TAK Server writes it"""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def result(package) -> dict:
    keywords = [keyword.keyword for keyword in package.keywords]
    submitted = timestamp(package.start_time)
    return {
        "UID": str(package.PrimaryKey),
        "Name": package.file_name or (keywords[0] if keywords else str(package.PrimaryKey)),
        "Hash": package.hash or str(package.PrimaryKey),
        "PrimaryKey": str(package.id),
        "SubmissionDateTime": submitted,
        "SubmissionUser": package.submitter or "anonymous",
        "CreatorUid": package.creator_uid or "",
        "Keywords": keywords,
        "MIMEType": package.mime_type or "application/x-zip-compressed",
        "Size": package.length or 0,
        "Tool": package.tool or "public",
        "Expiration": package.expiration if package.expiration is not None else -1,
    }


def response(packages: List) -> Response:
    """the public packages as /Marti/sync/search lists them"""
    results = [result(package) for package in packages or [] if package.private == 0]
    return Response(json.dumps({"resultCount": len(results), "results": results}), mimetype="application/json")


def resource(package) -> dict:
    keywords = [keyword.keyword for keyword in package.keywords]
    name = package.file_name or (keywords[0] if keywords else str(package.PrimaryKey))
    return {
        "uid": str(package.PrimaryKey),
        "hash": package.hash or str(package.PrimaryKey),
        "name": name,
        "filename": name,
        "keywords": keywords,
        "mimeType": package.mime_type or "application/x-zip-compressed",
        "size": package.length or 0,
        "submissionTime": timestamp(package.start_time),
        "submitter": package.submitter or "anonymous",
        "creatorUid": package.creator_uid or "",
        "tool": package.tool or "public",
        "expiration": package.expiration if package.expiration is not None else -1,
    }


def resources_response(packages: List) -> Response:
    """the public packages as /Marti/api/sync/search lists them"""
    results = [resource(package) for package in packages or [] if package.private == 0]
    # TAK Server: new ApiResponse<>(API_VERSION, Resource.class.getSimpleName(), results)
    body = {"version": "3", "type": "Resource", "data": results, "nodeId": config.nodeID}
    return Response(json.dumps(body), mimetype="application/json")
