# 固定版本镜像（P2-14）。
#
# 说明：这个 Dockerfile 是给"搬到服务器"准备的，本地没构建过——
# 构建前请确认：① AstrBot 版本锁在 4.29.1；② 补丁在启动前已贴回；
# ③ 数据目录挂卷；④ 端口只映射到 127.0.0.1。
#
#   docker build -t demo:4.29.1 .
#   docker run -d --name demo -p 127.0.0.1:22877:22877 \
#     -v D:\demo_data:/app/core/data demo:4.29.1
#
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    DASHBOARD_PORT=22877

# ffmpeg：语音消息要转 wav
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖先装，改代码不用重装
COPY instances/11111111-1111-4111-8111-111111111111/core/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# 代码 + 补丁（补丁在运行时由 entrypoint 贴回，见 ops/patches/patches.py）
COPY instances/11111111-1111-4111-8111-111111111111/core /app/core
COPY patches /app/patches
COPY ops /app/ops

# 数据目录：生产上请挂卷，不要把库打进镜像（.dockerignore 已排除 core/data）
VOLUME ["/app/core/data"]

EXPOSE 22877 6199

WORKDIR /app/core
# 容器内监听 0.0.0.0，靠 docker 的 -p 127.0.0.1:... 收敛
CMD ["python", "-u", "main.py"]
