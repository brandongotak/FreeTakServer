"""keep the names devices connect with in the server certificate

    python -m FreeTAKServer.core.util.server_cert_names [--add NAME ...] [--list]

The server certificate (8089, 8443) is issued to CN "server", which matches no
address a device uses, so clients that check the hostname (iTAK, TAK Aware,
anything using the platform TLS checks) reject it. FTS calls ensure() at
startup: it adds the enrollment domain, the configured address, this machine's
hostname and IP addresses, and any names given with --add (or in
FTS_SERVER_CERT_NAMES) to the certificate's subjectAltName. Names are only
ever added, so a name passed once with --add stays.

The certificate is re-issued with the same key, subject and expiry and signed
by the same CA, so existing client packages and enrolled devices keep working.
Old files are kept as *.bak-<timestamp>. Restart FTS to load a changed
certificate."""
import argparse
import datetime
import ipaddress
import shutil
import socket
import subprocess
from typing import List, Set, Union

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from OpenSSL import crypto

from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.core.util.renew_certs import backup
from FreeTAKServer.services.enrollment_service.certificate_signing import load_ca

config = MainConfig.instance()

GeneralName = Union[x509.DNSName, x509.IPAddress]


def parse_name(name: str):
    """a name as a subjectAltName entry, or None if it is not a usable host name or address"""
    name = name.strip().lower().rstrip(".")
    if not name or name in ("0.0.0.0", "::"):
        return None
    try:
        return x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        pass
    labels = name.split(".")
    if len(name) > 253 or not all(label and len(label) <= 63 and label.replace("-", "").isalnum()
                                  and not label.startswith("-") and not label.endswith("-") for label in labels):
        return None
    return x509.DNSName(name)


def machine_addresses() -> List[str]:
    """the addresses of this machine's interfaces, other than loopback and link-local"""
    addresses = []
    if shutil.which("hostname"):
        try:
            output = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            output = ""
        for word in output.split():
            try:
                address = ipaddress.ip_address(word.split("%")[0])
            except ValueError:
                continue
            if not (address.is_loopback or address.is_link_local):
                addresses.append(str(address))
    return addresses


def wanted_names(extra: List[str] = ()) -> Set[GeneralName]:
    from FreeTAKServer.core.util import enrollment_setup
    names = ["localhost", "127.0.0.1", str(config.UserConnectionIP),
             enrollment_setup.status().get("domain") or "", socket.gethostname(), socket.getfqdn()]
    names += [name for name in str(config.ServerCertNames).split(",")]
    names += machine_addresses()
    names += list(extra)
    return {parsed for parsed in map(parse_name, names) if parsed is not None}


def current_names(cert: x509.Certificate) -> List[GeneralName]:
    try:
        return list(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
    except x509.ExtensionNotFound:
        return []


def describe(name: GeneralName) -> str:
    return str(name.value)


def ensure(extra: List[str] = ()) -> List[str]:
    """add any missing names to the server certificate; returns the names added"""
    with open(config.pemDir, "rb") as pem_file:
        server_cert = x509.load_pem_x509_certificate(pem_file.read())
    have = set(current_names(server_cert))
    missing = wanted_names(extra) - have
    if not missing:
        return []

    with open(config.keyDir, "rb") as key_file:
        server_key = serialization.load_pem_private_key(key_file.read(), password=None)
    ca_cert, ca_key = load_ca(config.CA, config.CAkey)
    names = sorted(have | missing, key=lambda name: (isinstance(name, x509.IPAddress), describe(name)))

    builder = (
        x509.CertificateBuilder()
        .subject_name(server_cert.subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(minutes=5))
        # keep the expiry, but a server certificate must not outlive its CA
        .not_valid_after(min(server_cert.not_valid_after, ca_cert.not_valid_after))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        # serverAuth for devices; clientAuth because federation and the health
        # check present this certificate as a client
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
                       critical=False)
        # strict verifiers (Python 3.13+, some TLS stacks) require the key identifiers
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), critical=False)
    )
    replaced = (x509.SubjectAlternativeName, x509.ExtendedKeyUsage, x509.SubjectKeyIdentifier,
                x509.AuthorityKeyIdentifier)
    for extension in server_cert.extensions:
        if not isinstance(extension.value, replaced):
            builder = builder.add_extension(extension.value, critical=extension.critical)
    new_cert = builder.sign(ca_key, hashes.SHA256())

    backup(config.pemDir)
    with open(config.pemDir, "wb") as pem_file:
        pem_file.write(new_cert.public_bytes(serialization.Encoding.PEM))

    # server.p12 is the truststore in client packages; same export as renew_certs
    p12 = crypto.PKCS12()
    p12.set_privatekey(crypto.PKey.from_cryptography_key(server_key))
    p12.set_certificate(crypto.X509.from_cryptography(new_cert))
    p12.set_ca_certificates((crypto.X509.from_cryptography(ca_cert),))
    backup(config.p12Dir)
    with open(config.p12Dir, "wb") as p12_file:
        p12_file.write(p12.export(passphrase=config.password.encode()))
    return sorted(describe(name) for name in missing)


def listed_names() -> List[str]:
    with open(config.pemDir, "rb") as pem_file:
        return [describe(name) for name in current_names(x509.load_pem_x509_certificate(pem_file.read()))]


def main():
    parser = argparse.ArgumentParser(description="Add the names devices connect with to the server certificate.")
    parser.add_argument("--add", nargs="*", default=[], metavar="NAME",
                        help="extra domain names or IP addresses to include")
    parser.add_argument("--list", action="store_true", help="only show the names in the certificate")
    args = parser.parse_args()

    if not args.list:
        bad = [name for name in args.add if parse_name(name) is None]
        if bad:
            parser.error(f"not a domain name or IP address: {', '.join(bad)}")
        added = ensure(args.add)
        if added:
            print(f"added to the server certificate: {', '.join(added)}")
            print("restart FTS to load it: systemctl restart fts")
        else:
            print("the server certificate already has every name")
    print("names in the server certificate: " + ", ".join(listed_names()))


if __name__ == "__main__":
    main()
