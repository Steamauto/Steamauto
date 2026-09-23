# Steamauto-AetherSwap 本地开发与调试指南

本文档记录当前项目的本地开发工作流、WSL 挂载架构以及常用维护命令。

---

## 架构与核心开发逻辑

为保证线上生产服务器的纯净与安全性，本项目**禁止直接在远程服务器上边改代码边调试**。

### 核心工作流
1. **代码编辑（Windows 本地）**：
   - 所有代码开发、文件编辑与 Git 提交均在 Windows 本地工程目录（如 `D:\Projects\Steamapp`）进行。
2. **运行与测试（WSL2 + Docker 本地挂载）**：
   - 利用 WSL2（Ubuntu-24.04）环境运行 Docker 容器。
   - 通过 `docker-compose.override.yml` 将本地 Windows 目录（`/mnt/d/Projects/Steamapp`）完整直通挂载到容器内的 `/app` 目录。
   - **前端修改（HTML / CSS / JS）**：保存后在 Windows 浏览器刷新页面（`F5` 或 `Ctrl+F5`）即刻生效。
   - **后端修改（Python 路由 / 服务）**：修改保存后只需一条命令重启容器即可载入最新代码。
3. **访问入口**：
   - WSL2 自动打通 Localhost 端口转发，在 Windows 浏览器直接访问：
     👉 **`http://127.0.0.1:28472`**
4. **远程生产发布（GitHub 驱动）**：
   - 本地充分测试完毕后，提交并推送代码到 GitHub 仓库分支（如 `feature/aetherswap-fusion`）。
   - 仅在需要正式上线时，登录远程服务器（zouter2h2g）执行 `git pull` 并构建部署。远程服务器不再驻留任何临时测试容器。

---

## 常用本地命令速查

在 Windows PowerShell 或命令提示符中可直接执行以下命令：

### 1. 启动容器
```powershell
wsl -d Ubuntu-24.04 bash -c "cd /mnt/d/Projects/Steamapp && docker compose up -d"
```

### 2. 查看容器状态与实时日志
```powershell
# 查看状态
wsl -d Ubuntu-24.04 bash -c "cd /mnt/d/Projects/Steamapp && docker compose ps"

# 查看最近日志
wsl -d Ubuntu-24.04 bash -c "cd /mnt/d/Projects/Steamapp && docker compose logs -f --tail 50"
```

### 3. 重启容器（后端 Python 代码改动后）
```powershell
wsl -d Ubuntu-24.04 bash -c "cd /mnt/d/Projects/Steamapp && docker compose restart"
```

### 4. 重新构建镜像（修改了 requirements 或 Dockerfile 后）
```powershell
wsl -d Ubuntu-24.04 bash -c "cd /mnt/d/Projects/Steamapp && docker compose build"
```

### 5. 停止容器
```powershell
wsl -d Ubuntu-24.04 bash -c "cd /mnt/d/Projects/Steamapp && docker compose stop"
```

---

## 开发调试规范

1. **首次进入新手引导向导（Wizard Overlay）**：
   - 已彻底移除页面顶部的阻塞式新手向导弹窗（`onboard-wizard-overlay`）及检查函数。
   - 进入页面后直接展示主控制台，不再因 Steam 令牌或 Buff Cookie 缺失而弹出遮罩引导。
2. **轻量化依赖**：
   - 服务端容器使用极简依赖集合（`requirements-server.txt`），移除了 `chardet`, `qrcode[pil]`, `Pillow`, `colorlog` 等无用桌面/图形包，构建与运行更加轻量高效。
3. **本地开发配置隔离**：
   - `docker-compose.override.yml` 已被 `.gitignore` 忽略，确保本地的目录挂载配置不会影响线上生产部署。
