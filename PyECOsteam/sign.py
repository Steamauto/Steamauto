import base64
from collections import OrderedDict

from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15


def generate_rsa_signature(private_key_str, params):
    # 加载私钥
    private_key = RSA.import_key(private_key_str)

    # 排序参数
    sorted_params = OrderedDict(sorted(params.items()))

    # 拼接参数
    message = "&".join(
        f"{key}={value}" for key, value in sorted_params.items() if value is not None
    )

    # 使用SHA256哈希生成消息摘要
    hash_value = SHA256.new(message.encode("utf-8"))

    # 使用私钥对摘要进行签名
    signature = pkcs1_15.new(private_key).sign(hash_value)

    # 将签名转换为base64编码
    signature_base64 = base64.b64encode(signature).decode("utf-8")

    return signature_base64
