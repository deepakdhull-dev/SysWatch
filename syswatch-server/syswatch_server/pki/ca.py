from __future__ import annotations

import datetime
import io
import ipaddress
import logging
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ..exceptions import CertificateError

logger = logging.getLogger(__name__)

CA_VALIDITY_DAYS: int = 3650
LEAF_VALIDITY_DAYS: int = 365  #

CA_KEY_BITS: int = 4096
LEAF_KEY_BITS: int = 2048

RSA_PUBLIC_EXPONENT: int = 65537


@dataclass
class CertPaths:
    ca_cert: str = "/etc/syswatch/pki/ca/ca.crt"
    ca_key: str = "/etc/syswatch/pki/ca/ca.key"
    server_cert: str = "/etc/syswatch/pki/server/server.crt"
    server_key: str = "/etc/syswatch/pki/server/server.key"


def _generate_rsa_key(key_size: int) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(
        public_exponent=RSA_PUBLIC_EXPONENT,
        key_size=key_size,
    )


def _key_to_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _cert_to_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _not_after(days: int) -> datetime.datetime:
    return _now_utc() + datetime.timedelta(days=days)


def _make_name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _build_ca_cert(
    ca_key: rsa.RSAPrivateKey,
    cn: str = "syswatch-ca",
) -> x509.Certificate:
    ca_name = _make_name(cn)
    now = _now_utc()

    return (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(_not_after(CA_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def _build_server_cert(
    server_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    hostname: str,
    extra_san_ips: list[str] | None = None,
) -> x509.Certificate:
    san_names: list[x509.GeneralName] = [
        x509.DNSName(hostname),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]

    for ip_str in extra_san_ips or []:
        try:
            san_names.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
            logger.debug("Added SAN IP: %s", ip_str)
        except ValueError:
            logger.warning("Skipping invalid SAN IP: %r", ip_str)

    return (
        x509.CertificateBuilder()
        .subject_name(_make_name(hostname))
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now_utc())
        .not_valid_after(_not_after(LEAF_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_names),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def _build_agent_cert(
    agent_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    agent_id: str,
) -> x509.Certificate:
    return (
        x509.CertificateBuilder()
        .subject_name(_make_name(agent_id))
        .issuer_name(ca_cert.subject)
        .public_key(agent_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now_utc())
        .not_valid_after(_not_after(LEAF_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


class CertificateAuthority:
    def __init__(
        self,
        ca_cert: x509.Certificate,
        ca_key: rsa.RSAPrivateKey,
        paths: CertPaths,
    ) -> None:
        self._ca_cert = ca_cert
        self._ca_key = ca_key
        self._paths = paths

        self._ca_cert_pem: bytes = _cert_to_pem(ca_cert)

        logger.info(
            "CertificateAuthority loaded: CN=%s serial=%s valid_until=%s",
            ca_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
            ca_cert.serial_number,
            ca_cert.not_valid_after_utc.isoformat(),
        )

    @classmethod
    def initialize(
        cls,
        paths: CertPaths,
        hostname: str,
        extra_san_ips: list[str] | None = None,
    ) -> CertificateAuthority:
        existing = [p for p in (paths.ca_cert, paths.ca_key) if Path(p).exists()]
        if existing:
            raise CertificateError(
                f"CA files already exist: {existing}. "
                "Delete /etc/syswatch/server/ and re-run install.sh to regenerate. "
                "WARNING: all existing agent bundles will need to be re-issued."
            )

        logger.info("Generating CA key (%d-bit RSA)...", CA_KEY_BITS)
        ca_key = _generate_rsa_key(CA_KEY_BITS)
        ca_cert = _build_ca_cert(ca_key)
        logger.info(
            "CA certificate generated (valid until %s)",
            ca_cert.not_valid_after_utc.isoformat(),
        )

        logger.info(
            "Generating server key (%d-bit RSA) for hostname=%r...",
            LEAF_KEY_BITS,
            hostname,
        )
        server_key = _generate_rsa_key(LEAF_KEY_BITS)
        server_cert = _build_server_cert(
            server_key, ca_cert, ca_key, hostname, extra_san_ips
        )
        logger.info(
            "Server certificate generated (CN=%s SANs=%s)",
            hostname,
            [
                str(n)
                for n in server_cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
            ],
        )

        _write_pem(paths.ca_key, _key_to_pem(ca_key), mode=0o600)
        _write_pem(paths.ca_cert, _cert_to_pem(ca_cert), mode=0o644)
        _write_pem(paths.server_key, _key_to_pem(server_key), mode=0o600)
        _write_pem(paths.server_cert, _cert_to_pem(server_cert), mode=0o644)

        logger.info(
            "PKI initialized. Files written:\n"
            "  CA cert:     %s\n"
            "  CA key:      %s  (keep secret — never distribute)\n"
            "  Server cert: %s\n"
            "  Server key:  %s",
            paths.ca_cert,
            paths.ca_key,
            paths.server_cert,
            paths.server_key,
        )

        return cls(ca_cert=ca_cert, ca_key=ca_key, paths=paths)

    @classmethod
    def load(cls, paths: CertPaths) -> CertificateAuthority:

        for path, label in [
            (paths.ca_cert, "CA certificate"),
            (paths.ca_key, "CA private key"),
        ]:
            if not Path(path).exists():
                raise CertificateError(
                    f"PKI {label} not found at {path}. "
                    "Run install.sh (server path) to initialize the PKI."
                )

        logger.info("Loading CA certificate from %s", paths.ca_cert)
        ca_cert_pem = Path(paths.ca_cert).read_bytes()
        ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)

        logger.info("Loading CA private key from %s", paths.ca_key)
        ca_key_pem = Path(paths.ca_key).read_bytes()
        ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)

        if not isinstance(ca_key, rsa.RSAPrivateKey):
            raise CertificateError(
                f"CA key at {paths.ca_key} is not an RSA key. "
                "syswatch currently requires RSA keys."
            )

        return cls(ca_cert=ca_cert, ca_key=ca_key, paths=paths)

    def sign_agent(self, agent_id: str) -> tuple[bytes, bytes]:
        logger.info(
            "Signing agent certificate for agent_id=%r (%d-bit RSA)...",
            agent_id,
            LEAF_KEY_BITS,
        )

        agent_key = _generate_rsa_key(LEAF_KEY_BITS)
        agent_cert = _build_agent_cert(agent_key, self._ca_cert, self._ca_key, agent_id)

        logger.info(
            "Agent cert signed: CN=%r serial=%s valid_until=%s",
            agent_id,
            agent_cert.serial_number,
            agent_cert.not_valid_after_utc.isoformat(),
        )

        return _key_to_pem(agent_key), _cert_to_pem(agent_cert)

    def build_bundle(
        self,
        agent_id: str,
        server_host: str,
        server_port: int = 50051,
        services: list[str] | None = None,
    ) -> bytes:
        key_pem, cert_pem = self.sign_agent(agent_id)

        agent_config = _build_agent_yaml(
            agent_id=agent_id,
            server_host=server_host,
            server_port=server_port,
            services=services or [],
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ca.crt", self._ca_cert_pem.decode())
            zf.writestr("client.crt", cert_pem.decode())
            zf.writestr("client.key", key_pem.decode())
            zf.writestr("agent.yaml", agent_config)

        bundle_bytes = buf.getvalue()

        logger.info(
            "Bundle assembled for agent_id=%r (size=%d bytes, server=%s:%d, services=%s)",
            agent_id,
            len(bundle_bytes),
            server_host,
            server_port,
            services or [],
        )

        return bundle_bytes

    def ca_cert_pem(self) -> bytes:
        return self._ca_cert_pem

    def ca_expires_at(self) -> datetime.datetime:
        return self._ca_cert.not_valid_after_utc

    def days_until_ca_expiry(self) -> int:
        delta = self.ca_expires_at() - _now_utc()
        return max(0, delta.days)


def _write_pem(path: str, data: bytes, mode: int = 0o644) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    os.chmod(dest, mode)
    logger.debug("Wrote %d bytes to %s (mode=%04o)", len(data), path, mode)


def _build_agent_yaml(
    agent_id: str,
    server_host: str,
    server_port: int,
    services: list[str],
) -> str:
    config: dict[str, Any] = {
        "agent": {
            "agent_id": agent_id,
            "services": services,
        },
        "server": {
            "host": server_host,
            "port": server_port,
            "tls_enabled": True,
        },
        "certs": {
            "ca_cert": "/etc/syswatch/certs/ca.crt",
            "client_cert": "/etc/syswatch/certs/client.crt",
            "client_key": "/etc/syswatch/certs/client.key",
        },
        "collector": {
            "cpu_interval": 1.0,
            "ram_interval": 2.0,
            "disk_interval": 12.0,
            "network_interval": 1.0,
            "load_interval": 5.0,
            "service_interval": 10.0,
        },
        "sampler": {
            "frame_interval": 5.0,
        },
        "streamer": {
            "backoff_base": 1.0,
            "backoff_cap": 60.0,
            "backoff_jitter": 2.0,
            "send_queue_size": 512,
        },
    }

    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def _cli_init() -> None:
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        prog="python3 -m syswatch_server.pki.ca",
        description="Initialize the syswatch PKI (CA + server certificate).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Generate CA and server certificate.")
    init_p.add_argument("--hostname", required=True, help="Server FQDN for SAN.")
    init_p.add_argument(
        "--ip",
        action="append",
        dest="ips",
        default=[],
        metavar="IP",
        help="Additional IPs for server cert SAN (repeatable).",
    )
    init_p.add_argument(
        "--certs-dir",
        default="/etc/syswatch/server",
        help="Directory for generated files (default: /etc/syswatch/server).",
    )

    args = parser.parse_args()

    if args.command == "init":
        certs_dir = args.certs_dir.rstrip("/")
        paths = CertPaths(
            ca_cert=f"{certs_dir}/ca.crt",
            ca_key=f"{certs_dir}/ca.key",
            server_cert=f"{certs_dir}/server.crt",
            server_key=f"{certs_dir}/server.key",
        )
        try:
            CertificateAuthority.initialize(
                paths=paths,
                hostname=args.hostname,
                extra_san_ips=args.ips,
            )
            print(f"PKI initialized. Files written to {certs_dir}/")
        except CertificateError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    _cli_init()
