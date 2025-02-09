import argparse
import hashlib
import json
import os

import requests


def get_release_assets(repo, version, token):
    """获取 GitHub Release 的构建产物信息"""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.github.com/repos/{repo}/releases/tags/{version}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["assets"]


def calculate_sha256(url, token, file_path='cache'):
    """下载文件到磁盘并计算 SHA256 哈希值"""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, stream=True, allow_redirects=True)

    # 确保请求成功
    response.raise_for_status()

    # 将文件下载到磁盘
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                file.write(chunk)
    print(f"Downloaded {url} to {file_path} Size(MB): {os.path.getsize(file_path) / 1024 / 1024}")
    
    # 从磁盘读取文件并计算 SHA256 哈希值
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_sha256.update(chunk)

    os.remove(file_path)
    return hash_sha256.hexdigest()


def update_public_json(version, repo, token):
    """更新 public.json 文件中的下载链接和哈希值"""
    with open("public.json", "r") as f:
        data = json.load(f)

    # 获取 GitHub Release 的构建产物
    assets = get_release_assets(repo, version, token)

    # 更新 public.json 中的下载链接和哈希值
    for entry in data["versions"]:
        if entry["version"] == version:
            for asset in assets:
                platform = "windows" if "windows" in asset["name"].lower() else "linux"
                entry["download_url"][platform] = asset["browser_download_url"]
                entry["hash"][platform] = calculate_sha256(asset["browser_download_url"], token)
            break

    # 写回 public.json 文件
    with open("public.json", "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="版本号，例如 5.0.1")
    parser.add_argument("--repo", required=True, help="GitHub 仓库名称，例如 owner/repo")
    args = parser.parse_args()

    # 从环境变量中读取 GITHUB_TOKEN
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN 环境变量未设置")

    # 更新 public.json 文件
    update_public_json(args.version, args.repo, token)
