"""set up username/password certificate enrollment with a Let's Encrypt certificate

    python -m FreeTAKServer.core.util.enrollment_setup --domain tak.example.com [--email you@example.com]
    python -m FreeTAKServer.core.util.enrollment_setup --status

Used by the installer's --domain option and by the UI's configure page (through
the REST API service), so both take the same path: get a certificate with
certbot, point the fts-enrollment service at it and (re)start the service.
Needs root, which FTS runs as."""
import argparse
import json
import os
import re
import shutil
import subprocess
from typing import Dict, Tuple

from cryptography import x509

# read by the fts-enrollment systemd unit (EnvironmentFile=); the unit only
# starts once this file exists
ENV_FILE = "/etc/freetakhub/enrollment.env"
SERVICE_NAME = "fts-enrollment"
LETSENCRYPT_LIVE = "/etc/letsencrypt/live"
CERTBOT_TIMEOUT = 300

DOMAIN_PATTERN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def read_env_file() -> Dict[str, str]:
    values = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as env_file:
            for line in env_file:
                key, sep, value = line.strip().partition("=")
                if sep and not key.startswith("#"):
                    values[key] = value
    return values


def status() -> Dict[str, object]:
    """describe the current enrollment configuration for the UI"""
    env = read_env_file()
    domain = env.get("FTS_ENROLLMENT_DOMAIN", "")
    cert_expiry = None
    certfile = env.get("FTS_ENROLLMENT_CERTFILE", "")
    if certfile and os.path.exists(certfile):
        with open(certfile, "rb") as cert_file:
            cert_expiry = x509.load_pem_x509_certificate(cert_file.read()).not_valid_after.strftime("%Y-%m-%d")
    active = ""
    if shutil.which("systemctl"):
        active = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                                capture_output=True, text=True).stdout.strip()
    return {"configured": bool(domain), "domain": domain, "certExpiry": cert_expiry,
            "service": active or "unknown", "port": env.get("FTS_ENROLLMENT_PORT", "8446")}


def allow_ports_if_ufw_active(port: str) -> None:
    """open the HTTP challenge and enrollment ports on a firewall that is already on; never enable ufw"""
    if shutil.which("ufw") is None:
        return
    if "Status: active" not in subprocess.run(["ufw", "status"], capture_output=True, text=True).stdout:
        return
    for allowed in ("80", port):
        subprocess.run(["ufw", "allow", f"{allowed}/tcp"], capture_output=True)


def setup(domain: str, email: str = "", port: str = "8446") -> Tuple[bool, str]:
    """get a Let's Encrypt certificate for domain and start the enrollment service with it"""
    domain = domain.strip().lower()
    email = email.strip()
    if not DOMAIN_PATTERN.match(domain):
        return False, f"invalid domain name: {domain!r}"
    if email and not EMAIL_PATTERN.match(email):
        return False, f"invalid email address: {email!r}"
    if shutil.which("certbot") is None:
        return False, "certbot is not installed (apt install certbot)"

    allow_ports_if_ufw_active(port)
    # standalone mode answers the HTTP-01 challenge itself on port 80
    command = ["certbot", "certonly", "--standalone", "--non-interactive", "--agree-tos",
               "--keep-until-expiring", "-d", domain]
    command += ["--email", email] if email else ["--register-unsafely-without-email"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=CERTBOT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "certbot timed out; check that port 80 is reachable from the internet"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        return False, "certbot failed: " + " ".join(detail)

    cert_dir = os.path.join(LETSENCRYPT_LIVE, domain)
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    with open(ENV_FILE, "w") as env_file:
        env_file.write(f"FTS_ENROLLMENT_DOMAIN={domain}\n"
                       f"FTS_ENROLLMENT_PORT={port}\n"
                       f"FTS_ENROLLMENT_CERTFILE={cert_dir}/fullchain.pem\n"
                       f"FTS_ENROLLMENT_KEYFILE={cert_dir}/privkey.pem\n")

    restart = subprocess.run(["systemctl", "restart", SERVICE_NAME], capture_output=True, text=True)
    if restart.returncode != 0:
        return False, f"certificate obtained, but {SERVICE_NAME} failed to start: {restart.stderr.strip()}"
    expiry = status()["certExpiry"]
    return True, f"enrollment enabled for {domain} on port {port} (certificate valid until {expiry})"


def main():
    parser = argparse.ArgumentParser(description="Set up Let's Encrypt certificate enrollment.")
    parser.add_argument("--domain")
    parser.add_argument("--email", default="")
    parser.add_argument("--port", default="8446")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status or not args.domain:
        print(json.dumps(status(), indent=2))
        return
    ok, message = setup(args.domain, args.email, args.port)
    print(message)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
