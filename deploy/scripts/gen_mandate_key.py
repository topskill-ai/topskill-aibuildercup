#!/usr/bin/env python3
"""Generate the Ed25519 mandate signing key (PKCS8 PEM) for Secret Manager.

The private key is written to stdout only, so it can be piped straight into Secret Manager
without touching the disk:

    uv run --no-project --with cryptography python deploy/scripts/gen_mandate_key.py \
      | gcloud secrets create mandate-signing-key --data-file=- --replication-policy=automatic

To protect the key from ending up in a terminal scrollback or a log, the script refuses to run
when stdout is a terminal. The public key (JWK form, safe to share) is printed to stderr.
"""

from __future__ import annotations

import base64
import json
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main() -> int:
    if sys.stdout.isatty():
        print(
            "Refusing to print a private key to a terminal. Pipe stdout into "
            "`gcloud secrets create ... --data-file=-` instead.",
            file=sys.stderr,
        )
        return 2
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    sys.stdout.buffer.write(pem)
    sys.stdout.flush()
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": b64url(public_raw), "use": "sig", "alg": "EdDSA"}
    print(f"public JWK (kid is set by MANDATE_KID): {json.dumps(jwk)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
