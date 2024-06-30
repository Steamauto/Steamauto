from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa, dsa, ec
from cryptography.hazmat.backends import default_backend
import json
import base64
import re


def normalize_key(private_key_str):
    # Remove surrounding whitespace
    private_key_str = private_key_str.strip()

    # Add headers and footers if they are missing
    if not private_key_str.startswith('-----BEGIN'):
        private_key_str = '-----BEGIN PRIVATE KEY-----\n' + private_key_str
    if not private_key_str.endswith('-----END'):
        private_key_str = private_key_str + '\n-----END PRIVATE KEY-----'

    # Ensure proper line breaks
    private_key_str = re.sub(r'(.{64})', r'\1\n', private_key_str)

    return private_key_str


def generate_signature(private_key_str, params):
    # Normalize the key to ensure proper format
    private_key_str = normalize_key(private_key_str)

    # Load private key
    private_key = serialization.load_pem_private_key(
        private_key_str.encode('utf-8'),
        password=None,
        backend=default_backend()
    )

    # Prepare the message
    message_parts = []
    for key in sorted(params.keys(), key=str.lower):
        value = params[key]
        if isinstance(value, dict) or isinstance(value, list):
            message_parts.append(
                '{}={}'.format(key, json.dumps(value, sort_keys=False, ensure_ascii=False, separators=(',', ':'))))
        else:
            if value is None:
                continue
            message_parts.append('{}={}'.format(key, value))

    message = "&".join(message_parts)

    # Create the hash value
    hash_value = hashes.Hash(hashes.SHA256(), backend=default_backend())
    hash_value.update(message.encode('utf-8'))
    digest = hash_value.finalize()

    # Sign the digest
    if isinstance(private_key, rsa.RSAPrivateKey):
        signature = private_key.sign(
            digest,
            padding.PKCS1v15(),
            hashes.SHA256()
        )
    elif isinstance(private_key, dsa.DSAPrivateKey):
        signature = private_key.sign(
            digest,
            hashes.SHA256()
        )
    elif isinstance(private_key, ec.EllipticCurvePrivateKey):
        signature = private_key.sign(
            digest,
            ec.ECDSA(hashes.SHA256())
        )
    else:
        raise ValueError("Unsupported key type")

    # Convert signature to base64
    signature_base64 = base64.b64encode(signature).decode("utf-8")
    return signature_base64
