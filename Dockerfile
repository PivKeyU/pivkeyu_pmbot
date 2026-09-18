# 使用官方 Python 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件到工作目录
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制所有项目文件到工作目录
COPY . .

# 构建时注入版本信息，供机器人显示「当前运行的是哪个 commit」。
# 用 ARG + ENV 两步：ARG 只在构建期可见，ENV 才能被运行时的进程读到。
# 默认值留空，这样 `docker build .`（本地不传参数）不会失败。
# 位置放在 COPY . . 之后：改版本号只影响这一层，pip 安装层缓存不失效。
ARG BUILD_SHA=""
ARG BUILD_TIME=""
ENV BUILD_SHA=$BUILD_SHA
ENV BUILD_TIME=$BUILD_TIME

# 运行 bot
CMD ["python", "bot.py"]
