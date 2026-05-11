# ============================================================
# app/ap/signatures.py — HTTP Signatures for ActivityPub
# ============================================================
#
# ActivityPub uses HTTP Signatures (draft-cavage-http-signatures)
# to prove that a request really came from the actor it claims.
#
# How it works:
#   1. The sender builds a string from selected HTTP headers
#      (date, host, request-target, optionally digest)
#   2. The sender signs that string with their RSA private key
#   3. The signature is added to the Authorization header
#   4. The receiver fetches the sender's public key from their
#      Actor profile and verifies the signature
#
# We follow Mastodon's implementation for maximum compatibility:
#   - Algorithm: RSA-SHA256
#   - Required headers: (request-target) host date
#   - Body digest: SHA-256, included for POST requests
#
# Reference:
#   https://docs.joinmastodon.org/spec/security/
#   https://www.ietf.org/archive/id/draft-cavage-http-signatures-12.txt

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timezone
from email.utils import formatdate
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

if TYPE_CHECKING:
    pass


# ============================================================
# Key generation
# ============================================================

def generate_rsa_keypair() -> tuple[str, str]:
    """
    Generate a new 2048-bit RSA key pair.

    Returns (private_key_pem, public_key_pem) as strings.
    Both are PEM-encoded and safe to store in the database.
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


# ============================================================
# Signing outgoing requests
# ============================================================

def sign_request(
    method: str,
    url: str,
    body: bytes | None,
    private_key_pem: str,
    key_id: str,
) -> dict[str, str]:
    """
    Sign an outgoing HTTP request and return the headers to add.

    Args:
        method:          HTTP method in lowercase ("post", "get")
        url:             Full URL of the request
        body:            Request body bytes (None for GET)
        private_key_pem: PEM-encoded RSA private key of the sender
        key_id:          URL that points to the sender's public key,
                         e.g. "https://instance.example/users/maria#main-key"

    Returns a dict of headers to merge into the request:
        {"Date": "...", "Host": "...", "Digest": "...", "Signature": "..."}

    NOTE: header names in the returned dict use canonical casing (Date, Host,
    Digest, Signature) which is what HTTP clients expect. Internally we use
    lowercase keys for the signing string as required by the spec.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

    # Date header — required by the spec, used to prevent replay attacks
    date = formatdate(usegmt=True)

    # Build the headers dict with canonical (mixed) casing for HTTP delivery
    headers: dict[str, str] = {
        "Date": date,
        "Host": host,
    }

    # For POST requests, add a Digest header (SHA-256 of the body).
    signed_headers = "(request-target) host date"
    if body is not None:
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        headers["Digest"] = f"SHA-256={digest}"
        signed_headers += " digest"

    # Build the signing string.
    # The spec requires header names in lowercase in the signing string,
    # but our headers dict uses canonical (mixed) casing.
    # We create a lowercase lookup dict to bridge the two.
    headers_lower = {k.lower(): v for k, v in headers.items()}

    signing_parts = []
    for header in signed_headers.split():
        if header == "(request-target)":
            signing_parts.append(f"(request-target): {method.lower()} {path}")
        else:
            # header is already lowercase (from signed_headers string)
            # headers_lower has all keys in lowercase → no KeyError
            signing_parts.append(f"{header}: {headers_lower[header]}")
    signing_string = "\n".join(signing_parts)

    # Sign with RSA-SHA256
    private_key: RSAPrivateKey = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    signature_bytes = private_key.sign(
        signing_string.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")

    # Build the Signature header value (Mastodon-compatible format)
    headers["Signature"] = (
        f'keyId="{key_id}",'
        f'headers="{signed_headers}",'
        f'signature="{signature_b64}",'
        f'algorithm="rsa-sha256"'
    )

    return headers


# ============================================================
# Verifying incoming requests
# ============================================================

def _parse_signature_header(header: str) -> dict[str, str]:
    """Parse the Signature header into a dict of key=value pairs."""
    result = {}
    for match in re.finditer(r'(\w+)="([^"]*)"', header):
        result[match.group(1)] = match.group(2)
    return result


def verify_request(
    method: str,
    path: str,
    headers: dict[str, str],
    public_key_pem: str,
) -> bool:
    """
    Verify the HTTP Signature on an incoming request.

    Args:
        method:         HTTP method in lowercase
        path:           Request path (with query string if present)
        headers:        All request headers as a dict.
                        FastAPI passes these with lowercase keys, which is
                        what we expect here (the spec uses lowercase names).
        public_key_pem: PEM-encoded RSA public key of the claimed sender

    Returns True if the signature is valid, False otherwise.
    """
    try:
        # Normalize incoming headers to lowercase for safe lookup
        headers_lower = {k.lower(): v for k, v in headers.items()}

        sig_header = headers_lower.get("signature", "")
        if not sig_header:
            return False

        sig_params = _parse_signature_header(sig_header)
        signed_headers_list = sig_params.get("headers", "date").split()
        signature_b64 = sig_params.get("signature", "")

        if not signature_b64:
            return False

        # Rebuild the signing string from the actual request headers
        signing_parts = []
        for header in signed_headers_list:
            if header == "(request-target)":
                signing_parts.append(f"(request-target): {method.lower()} {path}")
            else:
                value = headers_lower.get(header, "")
                signing_parts.append(f"{header}: {value}")
        signing_string = "\n".join(signing_parts)

        # Verify the signature
        public_key: RSAPublicKey = serialization.load_pem_public_key(
            public_key_pem.encode("utf-8")
        )
        signature_bytes = base64.b64decode(signature_b64)
        public_key.verify(
            signature_bytes,
            signing_string.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True

    except Exception:
        return False
