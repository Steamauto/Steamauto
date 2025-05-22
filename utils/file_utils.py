import os
import chardet
import hashlib

# 用于解决读取文件时的编码问题
def get_encoding(file_path):
    if not os.path.exists(file_path):
        return "utf-8"
    with open(file_path, "rb") as f:
        data = f.read()
        charset = chardet.detect(data)["encoding"]
    return charset

def calculate_sha256(file_path: str) -> str:
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()
