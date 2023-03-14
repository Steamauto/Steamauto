import os


def writefile(name, text):
    with open(name, 'w', encoding='utf-8') as f:
        f.write(text)


def readfile(name):
    text = None
    with open(name, 'r', encoding='utf-8') as f:
        text = str(f.read())
    return text


def appendfile(name, text):
    if os.path.exists(name):
        with open(name, 'a', encoding='utf-8') as f:
            f.write(text)
    else:
        with open(name, 'w', encoding='utf-8') as f:
            f.close()


def get_mid_str(s, start_str, stop_str):
    # 查找左边文本的结束位置
    start_pos = s.find(start_str)
    if start_pos == -1:
        return None
    start_pos += len(start_str)
    # 查找右边文本的起始位置
    stop_pos = s.find(stop_str, start_pos)
    if stop_pos == -1:
        return None

    # 通过切片取出中间的文本
    return s[start_pos:stop_pos]
