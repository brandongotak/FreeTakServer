"""this module runs the certificate enrollment service

TAK clients connect to it with only a server address, username and password.
It is served with a publicly trusted certificate (e.g. Let's Encrypt) so the
client needs no truststore; it checks the credentials against the FTS system
users, signs the client's CSR with the FTS CA and returns the signed
certificate together with the CA, which the client then uses to connect to the
SSL CoT port"""
import hmac
import json
import sys
from pathlib import Path, PurePath

import eventlet
from cryptography.hazmat.primitives import serialization
from eventlet import listen, wrap_ssl, wsgi
from flask import Flask, Response, request
from sqlalchemy import create_engine, text
from werkzeug.utils import secure_filename

from FreeTAKServer.core.configuration.CreateLoggerController import CreateLoggerController
from FreeTAKServer.core.configuration.DatabaseConfiguration import DatabaseConfiguration
from FreeTAKServer.core.configuration.LoggingConstants import LoggingConstants
from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.core.util import device_profile
from FreeTAKServer.core.util import enroll_codes

from .certificate_signing import (ORGANIZATION, ORGANIZATIONAL_UNIT, EnrollmentError, load_ca,
                                  parse_csr, sign_client_csr, to_base64_der)

config = MainConfig.instance()

LOG_NAME = "FTS-Enrollment_Service"
logger = CreateLoggerController(LOG_NAME, logging_constants=LoggingConstants(log_name=LOG_NAME)).getLogger()

# seconds to stall a failed login, to slow down password guessing
FAILED_LOGIN_DELAY = 2

CERTIFICATE_CONFIG = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ns2:certificateConfig xmlns="http://bbn.com/marti/xml/config" xmlns:ns2="com.bbn.marti.config">
    <nameEntries>
        <nameEntry name="O" value="{ORGANIZATION}"/>
        <nameEntry name="OU" value="{ORGANIZATIONAL_UNIT}"/>
    </nameEntries>
</ns2:certificateConfig>"""

app = Flask(__name__)
engine = create_engine(DatabaseConfiguration().DataBaseConnectionString)


def authenticate():
    """return the username if the request carries valid system user credentials, else None"""
    auth = request.authorization
    if auth is None or not auth.username or not auth.password:
        return None
    with engine.connect() as connection:
        passwords = connection.execute(
            text('SELECT password FROM "SystemUser" WHERE name = :name'), {"name": auth.username}
        ).scalars().all()
    # passwords are stored as plain text by the system user API
    if any(stored and hmac.compare_digest(stored.encode(), auth.password.encode()) for stored in passwords):
        return auth.username
    # quick-connect QR codes carry an enrollment code in place of the password
    if passwords and enroll_codes.verify(auth.username, auth.password):
        return auth.username
    logger.info(f"failed enrollment login for {auth.username!r} from {request.remote_addr}")
    eventlet.sleep(FAILED_LOGIN_DELAY)
    return None


def unauthorized():
    return Response("unauthorized", 401, {"WWW-Authenticate": 'Basic realm="FreeTAKServer"'})


@app.route("/Marti/api/tls/config", methods=["GET"])
def certificate_config():
    """tells the client which subject entries to put in its CSR"""
    if authenticate() is None:
        return unauthorized()
    return Response(CERTIFICATE_CONFIG, 200, mimetype="application/xml")


@app.route("/Marti/api/tls/signClient/v2", methods=["POST"])
def sign_client():
    """signs the client's CSR and returns the certificate and CA chain"""
    username = authenticate()
    if username is None:
        return unauthorized()
    try:
        csr = parse_csr(request.get_data())
        ca_cert, ca_key = load_ca(config.CA, config.CAkey)
        # TAK clients that support channels pass a version parameter
        cert = sign_client_csr(csr, username, ca_cert, ca_key, config.EnrollmentCertValidityDays,
                               channels=request.args.get("version") is not None)
    except EnrollmentError as e:
        logger.error(f"enrollment for {username!r} rejected: {e}")
        return Response(str(e), 400)

    save_issued_certificate(username, request.args.get("clientUid", ""), cert)
    logger.info(f"issued certificate for {username!r} (clientUid {request.args.get('clientUid', '')!r})")

    accept = request.headers.get("Accept", "").lower()
    if not accept or "*/*" in accept or "application/json" in accept:
        body = {"signedCert": to_base64_der(cert), "ca0": to_base64_der(ca_cert)}
        return Response(json.dumps(body), 200, mimetype="application/json")
    if "application/xml" in accept:
        body = (f'<?xml version="1.0" encoding="UTF-8"?><enrollment>'
                f'<signedCert>{to_base64_der(cert)}</signedCert><ca>{to_base64_der(ca_cert)}</ca></enrollment>')
        return Response(body, 200, mimetype="application/xml")
    return Response("unsupported Accept header", 400)


@app.route("/Marti/api/tls/profile/enrollment", methods=["GET"])
def enrollment_profile():
    """clients ask for a device profile after enrolling; it enables channels for
    this server, without which ATAK doesn't show its channels screen"""
    if authenticate() is None:
        return unauthorized()
    package = device_profile.profile_package(device_profile.request_host(request.host), "Enrollment")
    return Response(package, 200, mimetype="application/zip",
                    headers={"Content-Disposition": "attachment; filename=profile.zip"})


def save_issued_certificate(username: str, client_uid: str, cert):
    """keep a copy of every issued certificate under certs/enrolled for auditing and revocation"""
    directory = Path(PurePath(config.certsPath, "enrolled"))
    directory.mkdir(parents=True, exist_ok=True)
    name = secure_filename(f"{username}_{client_uid or 'unknown'}_{cert.serial_number:x}.pem")
    (directory / name).write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main():
    if not config.EnrollmentCertFile or not config.EnrollmentKeyFile:
        logger.error("FTS_ENROLLMENT_CERTFILE and FTS_ENROLLMENT_KEYFILE must point to a publicly "
                     "trusted certificate and key (e.g. Let's Encrypt fullchain.pem and privkey.pem)")
        sys.exit(1)
    logger.info(f"enrollment service starting on port {config.EnrollmentPort}")
    wsgi.server(sock=wrap_ssl(listen(("0.0.0.0", int(config.EnrollmentPort))),
                              certfile=config.EnrollmentCertFile, keyfile=config.EnrollmentKeyFile,
                              server_side=True),
                site=app, log=logger)


if __name__ == "__main__":
    main()
