# 使用 Python 3.13 基础镜像
FROM python:3.13-slim

# 设置工作目录
WORKDIR /app

# 安装必要的系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    git \
    libasound2 \
    portaudio19-dev \
    gcc \
    python3-dev \
    libevdev-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制项目文件
COPY . .

# 安装项目依赖 (使用 uv)
RUN uv sync --frozen

# 暴露端口 (poe web 任务使用的是 10021)
EXPOSE 10021

# 默认启动命令：运行 poe web
# 注意：这需要容器内能访问到 tunelo
ENTRYPOINT ["uv", "run", "poe", "web"]
