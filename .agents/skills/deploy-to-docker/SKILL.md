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

## 指令详情

### 1. 发布到服务器
当用户说“发布到服务器”、“部署到 Docker”、“deploy to docker”时触发。

```bash
./scripts/deploy.sh
```

## 注意事项
- 确保本地已配置 `ssh docker` 的快捷访问。
- 确保 `data/` 目录已被忽略，以免同步时产生冲突。
- 默认推送到 `main` 分支。
