import base64
import json

from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15


def generate_rsa_signature(private_key_str, params):
    # 加载私钥
    private_key = RSA.import_key(private_key_str)

    # 拼接参数，遇到字典或列表则转换成JSON字符串
    message_parts = []
    for key in sorted(params.keys(),key=str.lower):
        value = params[key]
        if isinstance(value, dict) or isinstance(value,list):
            message_parts.append('{}={}'.format(key,json.dumps(value,sort_keys=False,ensure_ascii=False,separators=(',',':'))))
        else:
            if value is None:
                continue
            message_parts.append('{}={}'.format(key,value))

    message = "&".join(message_parts)

    # 使用SHA256哈希生成消息摘要
    hash_value = SHA256.new(message.encode("utf-8"))

    # 使用私钥对摘要进行签名
    signature = pkcs1_15.new(private_key).sign(hash_value)

    # 将签名转换为base64编码
    signature_base64 = base64.b64encode(signature).decode("utf-8")
    return signature_base64


