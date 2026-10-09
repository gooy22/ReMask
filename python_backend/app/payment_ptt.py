"""Pinned Billing PTT format, ported from the supplied FBPay modules.

Only ADD_CARD with encrypted PAN/CSC is supported. No device registration,
signature invention, plaintext fallback, CHARGE or certificate bypass.
Tokens and ephemeral key material must stay in request-local memory.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import struct
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _json(value) -> bytes:
    # Matches JSON.stringify for the observed string/bool/null input fields.
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _spki(key) -> bytes:
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _fingerprint(key) -> str:
    return 'fp:' + _b64(hashlib.sha256(_spki(key)).digest())


def _kdf(shared: bytes, party_u: str, party_v: str) -> bytes:
    parts = [struct.pack('>I', 1), shared]
    for value in ('A256GCM', party_u, party_v):
        encoded = value.encode('utf-8')
        parts.extend((struct.pack('>I', len(encoded)), encoded))
    parts.append(struct.pack('>I', 256))
    return hashlib.sha256(b''.join(parts)).digest()


def _validate_chain(chain, root, now):
    """Validate the observed leaf/intermediate/root chain without feature bypasses."""
    if not isinstance(chain, list) or len(chain) != 2:
        raise ValueError('CARD_PTT_TRUST_CHAIN_INVALID')
    try:
        certs = [x509.load_der_x509_certificate(base64.b64decode(c, validate=True))
                 for c in chain if isinstance(c, str) and len(c) <= 32768]
        if len(certs) != 2:
            raise ValueError()
        leaf, intermediate = certs
        for cert in (leaf, intermediate, root):
            if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
                raise ValueError()
            # Reject unknown critical extensions instead of interpreting them loosely.
            supported = {x509.ExtensionOID.BASIC_CONSTRAINTS, x509.ExtensionOID.KEY_USAGE,
                         x509.ExtensionOID.SUBJECT_KEY_IDENTIFIER, x509.ExtensionOID.AUTHORITY_KEY_IDENTIFIER}
            if any(ext.critical and ext.oid not in supported for ext in cert.extensions):
                raise ValueError()
        intermediate.verify_directly_issued_by(root)
        leaf.verify_directly_issued_by(intermediate)
        for cert in (intermediate, root):
            constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
            usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
            if not constraints.ca or not usage.key_cert_sign:
                raise ValueError()
        root_path = root.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length
        if root_path is not None and root_path < 1:
            raise ValueError()
        try:
            if leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError()
        except x509.ExtensionNotFound:
            pass
        key = leaf.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError()
        try:
            if not leaf.extensions.get_extension_for_class(x509.KeyUsage).value.key_agreement:
                raise ValueError()
        except x509.ExtensionNotFound:
            pass
        return key
    except Exception:
        # Certificate exceptions can include supplied data. Export only a stable code.
        raise ValueError('CARD_PTT_TRUST_CHAIN_INVALID') from None


def server_key_from_chain(chain):
    root = x509.load_pem_x509_certificate(Path(__file__).with_name('contracts')
                                         .joinpath('meta_payments_root_ca.pem').read_bytes())
    return _validate_chain(chain, root, datetime.now(timezone.utc))


def _encrypt_card_token(auth_data, secret_payload, server_key, *, nonce, ephemeral_key, iv):
    """Format primitive; separate deterministic arguments exist only for parity tests."""
    if (not isinstance(server_key, ec.EllipticCurvePublicKey)
            or not isinstance(server_key.curve, ec.SECP256R1)
            or len(iv) != 12):
        raise ValueError('CARD_PTT_KEY_INVALID')
    public = ephemeral_key.public_key()
    pem = public.public_bytes(serialization.Encoding.PEM,
                             serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')
    party_v = _fingerprint(server_key)
    header = {'alg': 'ECDH-ES', 'apu': '', 'apv': _b64(party_v.encode()),
              'enc': 'A256GCM', 'epk': {'crv': 'P-256', 'kty': 'EC', 'pem': pem}}
    auth = _b64(_json({'data': auth_data, 'nonce': nonce, 'op': 'ADD_CARD', 'ver': 1}))
    protected = _b64(_json(header))
    key = _kdf(ephemeral_key.exchange(ec.ECDH(), server_key), '', party_v)
    encrypted = AESGCM(key).encrypt(iv, _json(secret_payload), (protected + '.' + auth).encode())
    payload = '.'.join((auth, protected, '', _b64(iv), _b64(encrypted[:-16]), _b64(encrypted[-16:])))
    # BillingPTTUtils supplies no keyPairs/publicKey to modularGeneratePTT.
    return _b64(_json({'payload': payload, 'signatures': []}))


def encrypt_card_token(auth_data, secret_payload, chain):
    key = server_key_from_chain(chain)
    return _encrypt_card_token(auth_data, secret_payload, key, nonce=str(uuid.uuid4()),
                               ephemeral_key=ec.generate_private_key(ec.SECP256R1()), iv=secrets.token_bytes(12))
