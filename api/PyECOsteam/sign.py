from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
import json
import base64
import re


def normalize_key(private_key_str):
    # Remove surrounding whitespace
    private_key_str = private_key_str.strip()

    key_data = private_key_str.replace("-----BEGIN PRIVATE KEY-----\n", "").replace("\n-----END PRIVATE KEY-----", "").replace("\n", "")

    # Ensure proper line breaks every 64 characters
    key_data = re.sub(r"(.{64})", r"\1\n", key_data)

    # Reconstruct the key with headers and footers
    private_key_str = "-----BEGIN PRIVATE KEY-----\n" + key_data + "\n-----END PRIVATE KEY-----"

    return private_key_str


def generate_rsa_signature(private_key_str, params):
    # Normalize the key to ensure proper format
    private_key_str = normalize_key(private_key_str)

    # 加载私钥
    private_key = RSA.import_key(private_key_str)

    # 拼接参数，遇到字典或列表则转换成JSON字符串
    message_parts = []
    for key in sorted(params.keys(), key=str.lower):
        value = params[key]
        if isinstance(value, dict) or isinstance(value, list):
            message_parts.append("{}={}".format(key, json.dumps(value, sort_keys=False, ensure_ascii=False, separators=(",", ":"))))
        else:
            if value is None:
                continue
            message_parts.append("{}={}".format(key, value))

    message = "&".join(message_parts)

    # 使用SHA256哈希生成消息摘要
    hash_value = SHA256.new(message.encode("utf-8"))

    # 使用私钥对摘要进行签名
    signature = pkcs1_15.new(private_key).sign(hash_value)

    # 将签名转换为base64编码
    signature_base64 = base64.b64encode(signature).decode("utf-8")
    return signature_base64
