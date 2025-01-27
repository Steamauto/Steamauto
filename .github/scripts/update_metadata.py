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

def calculate_sha256(url, token):
    """计算文件的 SHA256 哈希值"""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, stream=True)
    sha256 = hashlib.sha256()
    
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            sha256.update(chunk)
    
    return sha256.hexdigest()

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
                entry["hash"][platform] = calculate_sha256(asset["url"], token)
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