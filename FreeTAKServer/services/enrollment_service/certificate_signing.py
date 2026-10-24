"""this module signs TAK client certificate signing requests (CSRs) with the
FTS CA, as used by username/password certificate enrollment"""
import base64
import binascii
import datetime
import re
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CSR_BEGIN = "-----BEGIN CERTIFICATE REQUEST-----"
CSR_END = "-----END CERTIFICATE REQUEST-----"
PEM_CERTIFICATE = re.compile(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)

# subject entries clients are told to use (see /Marti/api/tls/config); they
# match the subject of the certificates FTS generates itself
ORGANIZATION = "FreeTAKServer"
ORGANIZATIONAL_UNIT = "Core Dev"


class EnrollmentError(Exception):
    """raised when a CSR can't be signed for the enrolling user"""


def load_ca(ca_pem_path: str, ca_key_path: str) -> Tuple[x509.Certificate, RSAPrivateKey]:
    """load the FTS CA certificate and its private key

    FTS appends its CRL to ca.pem, so only the certificate block is parsed
    """
    with open(ca_pem_path, "rb") as ca_file:
        match = PEM_CERTIFICATE.search(ca_file.read())
    if match is None:
        raise EnrollmentError(f"no certificate found in {ca_pem_path}")
    ca_cert = x509.load_pem_x509_certificate(match.group(0))

    with open(ca_key_path, "rb") as key_file:
        ca_key = serialization.load_pem_private_key(key_file.read(), password=None)
    return ca_cert, ca_key


def parse_csr(body: bytes) -> x509.CertificateSigningRequest:
    """parse a CSR sent by a TAK client: base64 DER, with or without PEM headers"""
    text = body.decode("ascii", errors="ignore").replace(CSR_BEGIN, "").replace(CSR_END, "")
    try:
        csr = x509.load_der_x509_csr(base64.b64decode("".join(text.split()), validate=True))
    except (binascii.Error, ValueError) as e:
        raise EnrollmentError(f"invalid CSR: {e}") from e
    if not csr.is_signature_valid:
        raise EnrollmentError("CSR signature is invalid")
    return csr


def sign_client_csr(csr: x509.CertificateSigningRequest, username: str, ca_cert: x509.Certificate,
                    ca_key: RSAPrivateKey, validity_days: int) -> x509.Certificate:
    """issue a client certificate for the authenticated user

    the subject is built from the username rather than copied from the CSR, so
    a client can't obtain a certificate in someone else's name
    """
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, username),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZATION),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ORGANIZATIONAL_UNIT),
    ])
    now = datetime.datetime.utcnow()
    # never outlive the CA, which FTS creates with a one year lifetime
    not_after = min(now + datetime.timedelta(days=validity_days), ca_cert.not_valid_after)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        # backdated slightly to tolerate client clock skew
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    )
    return builder.sign(ca_key, hashes.SHA256())


def to_base64_der(cert: x509.Certificate) -> str:
    """encode a certificate the way TAK enrollment responses carry it: base64 DER, no PEM headers"""
    return base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode("ascii")
