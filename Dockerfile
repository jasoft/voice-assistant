# 使用 Python 3.13 基础镜像
FROM python:3.13-slim

# 设置工作目录
WORKDIR /app

# 安装必要的系统依赖和编译工具
RUN apt-get update && apt-get install -y \
    curl \
    git \
    libasound2 \
    portaudio19-dev \
    gcc \
    g++ \
    cmake \
    python3-dev \
    libevdev-dev \
    && rm -rf /var/lib/apt/lists/*

# 下载并编译 sqlite-simple 插件 (多阶段构建思想，但在一个层里完成以保持简单)
RUN git clone https://github.com/wangfenjin/simple.git /tmp/simple && \
    cd /tmp/simple && \
    mkdir build && cd build && \
    cmake .. && \
    make && \
    mkdir -p /app/third_party/simple && \
    # 查找编译产物并复制
    find . -name "libsimple.so" -exec cp {} /app/third_party/simple/ \; && \
    rm -rf /tmp/simple

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 复制项目文件
COPY . .

# 创建 data 目录（用于挂载和持久化数据）
RUN mkdir -p /app/data

# 复制启动脚本并添加执行权限
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# 再次确保 libsimple.so 存在 (防止本地 COPY 覆盖掉了刚刚生成的目录)
RUN [ -f /app/third_party/simple/libsimple.so ] || ( \
    git clone https://github.com/wangfenjin/simple.git /tmp/simple_rebuild && \
    cd /tmp/simple_rebuild && \
    mkdir build && cd build && \
    cmake .. && \
    make && \
    mkdir -p /app/third_party/simple && \
    find . -name "libsimple.so" -exec cp {} /app/third_party/simple/ \; && \
    rm -rf /tmp/simple_rebuild )

# 安装项目依赖
RUN uv sync --frozen

# 暴露端口
EXPOSE 10031 8080

# 声明 data 卷，可被外部映射
VOLUME ["/app/data"]

# 默认启动命令：运行启动脚本，同时启动 ptt-api 和 sqlite_web
ENTRYPOINT ["/app/start.sh"]
