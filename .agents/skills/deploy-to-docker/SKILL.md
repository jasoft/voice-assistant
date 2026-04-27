---
name: deploy-to-docker
description: 一键将本地语音助手代码发布到远程 Docker 服务器。
---
# Deploy to Docker Skill

一键将本地语音助手代码发布到远程 Docker 服务器。

## 适用场景
- 完成了代码修改，需要同步到服务器生效时。
- 修复了 Docker 配置或启动脚本时。
- 需要在生产环境验证最新功能时。

## 核心流程
1. **本地校验**：检查是否有未提交的更改。
2. **Git 推送**：将本地代码推送到 GitHub `main` 分支。
3. **远程拉取**：通过 SSH 在服务器执行 `git pull`。
4. **容器重启**：在服务器执行 `docker compose up -d --build`（可选）。
5. **状态验证**：检查容器是否正常运行并输出最近日志。

## 指令详情

### 1. 发布到服务器
当用户说“发布到服务器”、“部署到 Docker”、“deploy to docker”时触发。

```bash
# 步骤 1: 确保本地已推送 (如果有改动则提交)
git status --porcelain | grep -q . && (git add . && git commit -m "chore: auto-commit before deployment" && git push origin main) || echo "Local already clean or pushed."

# 步骤 2: 远程同步并重启
ssh docker "cd ~/voice-assistant && git pull && docker compose up -d --build"

# 步骤 3: 检查运行状态
ssh docker "cd ~/voice-assistant && docker compose ps"
```

## 注意事项
- 确保本地已配置 `ssh docker` 的快捷访问。
- 确保 `data/` 目录已被忽略，以免同步时产生冲突。
- 默认推送到 `main` 分支。
