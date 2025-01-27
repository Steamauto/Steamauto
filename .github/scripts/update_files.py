import argparse
import json
import re


def update_static_version(version):
    """更新 utils/static.py 文件中的 CURRENT_VERSION"""
    with open("utils/static.py", "r") as f:
        content = f.read()

    # 使用正则表达式替换版本号
    content = re.sub(r'CURRENT_VERSION\s*=\s*["\']\d+\.\d+\.\d+["\']', f'CURRENT_VERSION = "{version}"', content)

    with open("utils/static.py", "w") as f:
        f.write(content)


def update_public_json(args):
    """更新 public.json 文件，添加新版本信息"""
    try:
        # 尝试读取 public.json 文件
        with open("public.json", "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        # 如果文件不存在，初始化一个空字典
        data = {"versions": []}

    # 确保 data["versions"] 是一个列表
    if not isinstance(data.get("versions"), list):
        data["versions"] = []
    changelog = args.changelog
    # 创建新版本信息
    new_version = {
        "version": args.version,
        "changelog": changelog,
        "level": args.level,
        "significance": args.significance,
        "download_url": {},  # 初始化为空字典
        "hash": {},  # 初始化为空字典
    }

    # 将新版本插入到列表的开头
    data["versions"].insert(0, new_version)

    # 写回 public.json 文件
    with open("public.json", "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="版本号，例如 5.0.1")
    parser.add_argument("--changelog", required=True, help="更新日志")
    parser.add_argument("--level", required=True, help="发布级别（stable/beta/alpha）")
    parser.add_argument("--significance", required=True, help="重要性级别（minor/normal/important/critical）")
    args = parser.parse_args()

    # 更新文件
    update_static_version(args.version)
    update_public_json(args)