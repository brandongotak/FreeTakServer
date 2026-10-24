"""renew the FTS CA and server certificates in place

    python -m FreeTAKServer.core.util.renew_certs [--ca-days N] [--server-days N]

Both certificates are re-issued with their existing keys and subjects and a
new expiry, so every certificate the CA has already signed (client packages,
enrolled clients, the server certificate) keeps validating against it and no
client has to be re-provisioned. Old files are kept as *.bak-<timestamp>.
Restart FTS afterwards so it loads the new certificates."""
import argparse
import datetime
import shutil

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from OpenSSL import crypto

from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.services.enrollment_service.certificate_signing import PEM_CERTIFICATE, load_ca

config = MainConfig.instance()


def backup(path) -> None:
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    shutil.copy2(path, f"{path}.bak-{stamp}")


def reissue(cert: x509.Certificate, issuer: x509.Certificate, signing_key, days: int) -> x509.Certificate:
    """copy cert with a new serial and expiry, keeping its subject, key and extensions"""
    builder = (
        x509.CertificateBuilder()
        .subject_name(cert.subject)
        .issuer_name(issuer.subject)
        .public_key(cert.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(cert.not_valid_before)
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=days))
    )
    for extension in cert.extensions:
        builder = builder.add_extension(extension.value, critical=extension.critical)
    return builder.sign(signing_key, hashes.SHA256())


def renew_ca(days: int) -> x509.Certificate:
    """re-sign the self-signed CA, keeping the CRL FTS appends after it in ca.pem"""
    ca_cert, ca_key = load_ca(config.CA, config.CAkey)
    new_ca = reissue(ca_cert, ca_cert, ca_key, days)

    with open(config.CA, "rb") as ca_file:
        contents = ca_file.read()
    backup(config.CA)
    with open(config.CA, "wb") as ca_file:
        ca_file.write(PEM_CERTIFICATE.sub(new_ca.public_bytes(serialization.Encoding.PEM).strip(), contents, count=1))
    print(f"CA renewed: valid until {new_ca.not_valid_after:%Y-%m-%d}")
    return new_ca


def renew_server_cert(days: int, ca_cert: x509.Certificate, ca_key) -> None:
    """re-issue server.pem and rebuild server.p12, the truststore FTS puts in client packages"""
    with open(config.pemDir, "rb") as pem_file:
        server_cert = x509.load_pem_x509_certificate(pem_file.read())
    with open(config.keyDir, "rb") as key_file:
        server_key = serialization.load_pem_private_key(key_file.read(), password=None)
    # a server certificate must not outlive the CA that signed it
    days = min(days, (ca_cert.not_valid_after - datetime.datetime.utcnow()).days)
    new_cert = reissue(server_cert, ca_cert, ca_key, days)

    backup(config.pemDir)
    with open(config.pemDir, "wb") as pem_file:
        pem_file.write(new_cert.public_bytes(serialization.Encoding.PEM))

    # same pyOpenSSL PKCS12 export FTS uses, so clients read it the same way
    p12 = crypto.PKCS12()
    p12.set_privatekey(crypto.PKey.from_cryptography_key(server_key))
    p12.set_certificate(crypto.X509.from_cryptography(new_cert))
    p12.set_ca_certificates((crypto.X509.from_cryptography(ca_cert),))
    backup(config.p12Dir)
    with open(config.p12Dir, "wb") as p12_file:
        p12_file.write(p12.export(passphrase=config.password.encode()))
    print(f"server certificate renewed: valid until {new_cert.not_valid_after:%Y-%m-%d}")


def main():
    parser = argparse.ArgumentParser(description="Renew the FTS CA and server certificates in place.")
    parser.add_argument("--ca-days", type=int, default=config.CACertValidityDays)
    parser.add_argument("--server-days", type=int, default=config.ServerCertValidityDays)
    args = parser.parse_args()

    new_ca = renew_ca(args.ca_days)
    _, ca_key = load_ca(config.CA, config.CAkey)
    renew_server_cert(args.server_days, new_ca, ca_key)
    print("restart FTS to load the renewed certificates")


if __name__ == "__main__":
    main()
