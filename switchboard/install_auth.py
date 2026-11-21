"""Local controller keys and short-lived, in-memory SSH credential grants.

Only ciphertext crosses the hub. The browser obtains its encryption key directly from
this computer's loopback dashboard, independently of the hub or the network.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEYS = {}
_GRANTS = {}
_SEEN = {}


def key(config):
    path = config.home / 'install-auth-key.pem'
    if str(path) not in _KEYS:
        try:
            private = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except FileNotFoundError:
            private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            data = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as file:
                    file.write(data)
            except FileExistsError:
                private = serialization.load_pem_private_key(path.read_bytes(), password=None)
        _KEYS[str(path)] = private
    return _KEYS[str(path)]


def public_key(config) -> dict:
    der = key(config).public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {'controller': config.node_name, 'key_id': hashlib.sha256(der).hexdigest(), 'spki': base64.b64encode(der).decode()}


def decrypt(config, envelope: dict, profile: str, target: str, tool: str) -> dict:
    try:
        if len(json.dumps(envelope)) > 12000 or envelope['key_id'] != public_key(config)['key_id']:
            raise ValueError()
        decode = lambda v: base64.b64decode(v, validate=True)
        aes = key(config).decrypt(decode(envelope['wrapped_key']), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        data = json.loads(AESGCM(aes).decrypt(decode(envelope['iv']), decode(envelope['ciphertext']), b'switchboard-install-auth-v1'))
        now = time.time()
        if not now <= float(data['expires']) <= now + 360 or (data['profile'], data['target_node'], data['tool']) != (profile, target, tool):
            raise ValueError()
        nonce = str(data['nonce'])
        for old in [n for n, until in _SEEN.items() if until < now]:
            _SEEN.pop(old, None)
        if not nonce or nonce in _SEEN:
            raise ValueError()
        _SEEN[nonce] = float(data['expires'])
        if not isinstance(data['username'], str) or not data['username'] or len(data['username']) > 128 or not isinstance(data['password'], str) or not data['password'] or len(data['password']) > 1024:
            raise ValueError()
        return {'username': data['username'], 'password': data['password']}
    except Exception:
        raise ValueError('Credential request is invalid, expired or already used. Re-enter credentials in the local Tazrivo app.') from None


def grant(config, profile: str, target: str, tool: str, credentials: dict) -> str:
    now = time.monotonic()
    for old in [handle for handle, data in _GRANTS.items() if data['expires'] < now]:
        _GRANTS.pop(old, None)
    handle = secrets.token_urlsafe(32)
    _GRANTS[handle] = {'home': str(config.home), 'profile': profile, 'target': target, 'tool': tool,
                       'expires': now + 300, 'credentials': credentials}
    import asyncio
    try:
        asyncio.get_running_loop().call_later(300, _GRANTS.pop, handle, None)
    except RuntimeError:
        pass
    return handle


def consume(config, handle: str, profile: str, target: str, tool: str) -> dict:
    data = _GRANTS.pop(handle, None)
    if not data or data['expires'] < time.monotonic() or (data['home'], data['profile'], data['target'], data['tool']) != (str(config.home), profile, target, tool):
        raise ValueError('Administrator authentication expired or was used. Authenticate again in the installation dialog.')
    return data['credentials']
