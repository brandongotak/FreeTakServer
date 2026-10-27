"""the reply to /Marti/sync/search, shaped like TAK Server's

ATAK reads FTS's old reply, but stricter clients (TAKX's Java client) expect
TAK Server's JSON: an application/json body, Hash as the SHA-256 the content
is fetched by, and the Tool and Expiration fields."""
import json
from typing import List

from flask import Response


def result(package) -> dict:
    keywords = [keyword.keyword for keyword in package.keywords]
    submitted = package.start_time.strftime("%Y-%m-%dT%H:%M:%S.") + f"{package.start_time.microsecond // 1000:03d}Z"
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
    """the public packages, as TAK Server lists them"""
    results = [result(package) for package in packages or [] if package.private == 0]
    return Response(json.dumps({"resultCount": len(results), "results": results}), mimetype="application/json")
