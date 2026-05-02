# 使用 Python 3.13 基础镜像
FROM python:3.13-slim

# 设置时区
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 安装必要的系统依赖和编译工具
RUN apt-get update && apt-get install -y \
    curl \
    git \
    unzip \
    libasound2 \
    portaudio19-dev \
    gcc \
    g++ \
    cmake \
    python3-dev \
    libevdev-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制项目核心文件
COPY . .

# 创建 data 目录（用于挂载和持久化数据）
RUN mkdir -p /app/data

# 赋予执行权限
RUN chmod +x /app/start.sh

# 安装项目依赖
RUN uv sync --frozen

# 暴露端口
EXPOSE 10031 18090

# 声明 data 卷，可被外部映射
VOLUME ["/app/data"]

# 默认启动命令：运行启动脚本，同时启动 ptt-api 和 sqlite_web
ENTRYPOINT ["/app/start.sh"]
