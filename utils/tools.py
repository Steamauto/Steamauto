import chardet


# 用于解决读取文件时的编码问题
def get_encoding(file_path):
    with open(file_path, "rb") as f:
        data = f.read(4)
        charset = chardet.detect(data)['encoding']
    return charset
