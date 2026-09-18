"""镜像更新核心服务（纯服务层：无 UI、无文案渲染）。

机器人跑在容器里，但**不挂载 docker.sock**，所以它自己不能拉镜像、更不能重建容器。
真正干活的是同网络里的 watchtower，本模块只做三件事：

1. 从 Docker Hub Registry API 读 `latest` 的 digest（= 远端版本指纹）；
2. 和上次记在 ``app_meta['image_last_digest']`` 里的指纹比较，判断有没有新版本；
3. 通过 watchtower 的 HTTP API（``POST /v1/update``）请求它去更新。

设计约束（改动前请先读一遍）：

* ``check_for_update()`` **绝不往外抛异常** —— 网络失败写进 ``status.error``，
  UI 一定要能安全展示；
* ``fetch_remote_digest()`` 走标准库 ``urllib`` + TLS 校验（公网数据必须验证书）；
* token 只出现在请求头里，**任何日志都不许打印**；
* 本模块不做文案渲染（那是 ``utils/copy.py`` 的职责），只返回纯文本状态。
"""

import asyncio
import hashlib
import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from database import models as db

logger = logging.getLogger(__name__)

__all__ = [
    "DOCKER_HUB_AUTH_URL",
    "DOCKER_HUB_REGISTRY",
    "WATCHTOWER_DEFAULT_URL",
    "MANIFEST_ACCEPT",
    "HTTP_TIMEOUT",
    "DEFAULT_IMAGE_REPO",
    "DEFAULT_IMAGE_TAG",
    "IMAGE_DIGEST_META_KEY",
    "ImageUpdateError",
    "ImageUpdateStatus",
    "detect_container",
    "resolve_image",
    "fetch_remote_digest",
    "check_for_update",
    "remember_current_digest",
    "trigger_watchtower_update",
    "format_image_status",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

DOCKER_HUB_AUTH_URL = "https://auth.docker.io/token"
DOCKER_HUB_REGISTRY = "https://registry-1.docker.io/v2"
WATCHTOWER_DEFAULT_URL = "http://watchtower:8080"
WATCHTOWER_UPDATE_PATH = "/v1/update"

# Accept 必须一次给出多个值：OCI index + 两种 Docker manifest。
# 只写其中一个时 registry 可能直接回 406。
MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)

HTTP_TIMEOUT = 15  # 秒

DEFAULT_IMAGE_REPO = "pivkeyu/pivkeyu_pmbot"
DEFAULT_IMAGE_TAG = "latest"

# app_meta 里存上一次见过的远端 digest 的 key（基线）
IMAGE_DIGEST_META_KEY = "image_last_digest"

_USER_AGENT = "pivkeyu-pmbot-image-update/1.0"

# TLS 上下文惰性创建：模块 import 时绝不做任何 IO。
_TLS_CONTEXT = None


# --------------------------------------------------------------------------- #
# 数据类 / 异常
# --------------------------------------------------------------------------- #


class ImageUpdateError(Exception):
    """``fetch_remote_digest`` / ``trigger_watchtower_update`` 的失败信号。

    message 是「给人看的简短中文说明」，不含 token、不含堆栈。
    """


@dataclass
class ImageUpdateStatus:
    """一次「检查镜像更新」的结果。"""

    in_container: bool          # 是否运行在 Docker 容器里
    image_repo: str             # 例如 pivkeyu/pivkeyu_pmbot
    image_tag: str              # 例如 latest
    local_sha: str              # 构建时注入的 BUILD_SHA（可能为空）
    remote_digest: str          # Docker Hub 上 latest 的 digest（可能为空）
    known_digest: str           # 上次记录在 app_meta 里的 digest（可能为空）
    update_available: bool      # 是否检测到新版本
    error: str = ""             # 出错时的简短说明（不抛异常给 UI）

    @property
    def short_local(self) -> str:
        return self.local_sha[:12] if self.local_sha else "-"

    @property
    def short_remote(self) -> str:
        return self.remote_digest[:12] if self.remote_digest else "-"

    @property
    def image(self) -> str:
        """`repo:tag`，方便 UI / 文案直接拼。"""
        return f"{self.image_repo}:{self.image_tag}"


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _tls_context() -> ssl.SSLContext:
    """公网请求用的 TLS 上下文（**必须校验**证书与主机名）。

    绝不用 ``ssl._create_unverified_context()``：这里的响应决定要不要重建线上
    容器，被中间人篡改的代价太高。默认上下文已经开启 CERT_REQUIRED +
    check_hostname，两行赋值只是把意图写死，防止以后被改掉。
    """
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        _TLS_CONTEXT = ctx
    return _TLS_CONTEXT


def _urlopen(request: urllib.request.Request, timeout: float = HTTP_TIMEOUT):
    """打开请求；https 一律带上校验用的 TLS 上下文。"""
    if request.full_url.lower().startswith("https://"):
        return urllib.request.urlopen(request, timeout=timeout, context=_tls_context())
    return urllib.request.urlopen(request, timeout=timeout)


def _redact_url(url: str) -> str:
    """日志里用的 URL：去掉 userinfo 与 query，避免任何凭据外泄。"""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))
    except Exception:  # pragma: no cover - 纯防御，解析失败也不该抛
        return "<watchtower url>"


def _split_image_ref(image: str) -> tuple[str, str]:
    """把 ``repo:tag`` 拆开；带端口的 registry（host:5000/repo）不会被误拆。"""
    image = (image or "").strip()
    if not image:
        return "", ""
    if "@" in image:  # repo@sha256:... 这种没有 tag，按 digest 处理更安全
        repo, _, digest = image.partition("@")
        return repo.strip(), digest.strip()
    head, sep, tail = image.rpartition(":")
    if sep and "/" not in tail:
        return head.strip(), tail.strip()
    return image, ""


def _local_sha() -> str:
    return (os.environ.get("BUILD_SHA") or "").strip()


def _short_reason(exc: BaseException, limit: int = 160) -> str:
    """异常 → 一句短说明（UI 要展示，不能把整段堆栈塞进去）。"""
    text = str(exc).strip() or exc.__class__.__name__
    text = text.replace("\n", " ")
    return text[:limit]


# --------------------------------------------------------------------------- #
# 环境探测 / 镜像名
# --------------------------------------------------------------------------- #


def detect_container() -> bool:
    """判断自己是不是跑在容器里。

    三条信号，任一命中即真：
      1. ``/proc/self/cgroup`` 出现 ``docker`` 或 ``kubepods``（Linux 容器）；
      2. ``/.dockerenv`` 存在；
      3. ``RUNNING_IN_DOCKER=1``（手动部署时留的显式开关）。

    ⚠️ Windows 上 ``/proc`` 与 ``/.dockerenv`` 都不存在，所以每步都用
    try/except 兜住：开发机（Windows）直接返回 False，绝不抛异常。
    """
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8", errors="replace") as fh:
            cgroup = fh.read()
        if "docker" in cgroup or "kubepods" in cgroup:
            return True
    except (OSError, ValueError):
        pass

    try:
        if os.path.exists("/.dockerenv"):
            return True
    except OSError:
        pass

    flag = (os.environ.get("RUNNING_IN_DOCKER") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True

    return False


def resolve_image() -> tuple[str, str]:
    """要检查的镜像 ``(repo, tag)``。

    优先级：``UPDATE_IMAGE_REPO``/``UPDATE_IMAGE_TAG`` → ``UPDATE_IMAGE``
    （``repo:tag`` 形式）→ 内置默认值。单项缺失时另一项仍可独立覆盖。
    """
    repo = (os.environ.get("UPDATE_IMAGE_REPO") or "").strip()
    tag = (os.environ.get("UPDATE_IMAGE_TAG") or "").strip()

    if not repo or not tag:
        parsed_repo, parsed_tag = _split_image_ref(os.environ.get("UPDATE_IMAGE") or "")
        if not repo:
            repo = parsed_repo
        if not tag:
            tag = parsed_tag

    return repo or DEFAULT_IMAGE_REPO, tag or DEFAULT_IMAGE_TAG


# --------------------------------------------------------------------------- #
# Docker Hub
# --------------------------------------------------------------------------- #


def _fetch_remote_digest_sync(repo: str, tag: str) -> str:
    """同步实现（在 ``asyncio.to_thread`` 里跑，别直接调用）。"""
    repo = (repo or "").strip()
    tag = (tag or "").strip()
    if not repo or not tag:
        raise ImageUpdateError("镜像名没写全呢，还要 repo 和 tag。")

    safe_repo = urllib.parse.quote(repo, safe="/")
    safe_tag = urllib.parse.quote(tag, safe="")

    # 1) 换匿名 pull token（公开镜像不需要登录）
    auth_url = (
        f"{DOCKER_HUB_AUTH_URL}?service=registry.docker.io"
        f"&scope={urllib.parse.quote(f'repository:{repo}:pull', safe='')}"
    )
    token = _request_json_token(auth_url)

    # 2) 取 manifest，读 Docker-Content-Digest
    manifest_url = f"{DOCKER_HUB_REGISTRY}/{safe_repo}/manifests/{safe_tag}"
    request = urllib.request.Request(
        manifest_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": MANIFEST_ACCEPT,
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with _urlopen(request) as response:
            digest = (response.headers.get("Docker-Content-Digest") or "").strip()
            body = response.read()
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", "?")
        if code in (401, 403):
            raise ImageUpdateError(
                f"Docker Hub 拒绝了取指纹的请求（HTTP {code}），镜像可能是私有的。"
            ) from exc
        if code == 404:
            raise ImageUpdateError(
                f"Docker Hub 上没有这个镜像或这个标签（HTTP 404）。"
            ) from exc
        if code == 406:
            raise ImageUpdateError(
                "Docker Hub 拒绝了 manifest 的 Accept 头（HTTP 406）。"
            ) from exc
        raise ImageUpdateError(f"Docker Hub 返回了 HTTP {code}。") from exc
    except urllib.error.URLError as exc:
        raise ImageUpdateError(
            f"连不上 Docker Hub（{_short_reason(exc.reason)}）。"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise ImageUpdateError(f"连不上 Docker Hub（{_short_reason(exc)}）。") from exc

    if not digest:
        # 极少见：registry 没给响应头，就自己对 body 算一遍摘要
        # （schema2 manifest 的 digest 就是 body 的 sha256）
        digest = "sha256:" + hashlib.sha256(body).hexdigest()

    if not digest.startswith("sha256:"):
        raise ImageUpdateError("Docker Hub 返回的指纹格式不对。")
    return digest


def _request_json_token(auth_url: str) -> str:
    request = urllib.request.Request(auth_url, headers={"User-Agent": _USER_AGENT})
    try:
        with _urlopen(request) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", "?")
        raise ImageUpdateError(f"Docker Hub 拒绝了取 token 的请求（HTTP {code}）。") from exc
    except urllib.error.URLError as exc:
        raise ImageUpdateError(
            f"连不上 Docker Hub（{_short_reason(exc.reason)}）。"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise ImageUpdateError(f"连不上 Docker Hub（{_short_reason(exc)}）。") from exc

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError) as exc:
        raise ImageUpdateError("Docker Hub 的 token 响应不是合法 JSON。") from exc

    token = (payload.get("token") or payload.get("access_token") or "").strip()
    if not token:
        raise ImageUpdateError("Docker Hub 没有返回可用的 token。")
    return token


async def fetch_remote_digest(repo: str, tag: str) -> str:
    """查 Docker Hub，返回该 tag 当前的 digest（``sha256:...``）。

    用标准库 ``urllib.request`` 在 ``asyncio.to_thread`` 里跑（不阻塞事件循环），
    失败一律抛 ``ImageUpdateError``。
    """
    return await asyncio.to_thread(_fetch_remote_digest_sync, repo, tag)


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #


async def remember_current_digest(digest: str) -> None:
    """把 digest 写进 ``app_meta['image_last_digest']``。

    在「刚完成更新」或「首次建立基线」时调用。
    """
    digest = (digest or "").strip()
    if not digest:
        return
    await db.set_app_meta(IMAGE_DIGEST_META_KEY, digest)


async def check_for_update() -> ImageUpdateStatus:
    """检查远端镜像有没有新版本。

    **绝不往外抛异常**：网络、解析、数据库的问题都写进 ``status.error``，
    并让 ``update_available=False``。

    判定规则：
      * 有 ``known_digest``（上次记下的指纹）→ ``remote != known`` 即视为有更新；
      * 没有 ``known_digest``（首次运行）→ 把远端指纹存成基线并返回 False
        （启动/首次检查时误报「有新版本」比漏报更烦人）；
      * ``local_sha`` 是 git commit、``remote_digest`` 是镜像摘要，
        **两者不可比**，任何情况下都不拿它们互相比较。

    为什么要「基线」而不是直接拿本地比：bot 的 ``BUILD_SHA`` 是构建时的 git
    commit，容器里的镜像可能是几周前拉的；而 Docker Hub 上 ``latest`` 的
    digest 只能和「上一次见到的 digest」相比才有意义（它是可变标签）。没有
    基线时最诚实的做法是把当前远端值记下来当起点，而不是张嘴就报「有更新」——
    那种误报会让主人在什么都没变的情况下被叫去重建容器，几次之后就再也不信
    这个提醒了。代价是：**基线建立前发生的发布，要等下一次发布才会被发现**；
    真正的「有没有新版本」从第二次检查开始生效。
    """
    in_container = detect_container()
    repo, tag = resolve_image()
    status = ImageUpdateStatus(
        in_container=in_container,
        image_repo=repo,
        image_tag=tag,
        local_sha=_local_sha(),
        remote_digest="",
        known_digest="",
        update_available=False,
        error="",
    )

    # 1) 上次记下的指纹
    #    ⚠️ 读失败时**绝不去写基线**：磁盘上很可能存着一个旧指纹，若此时把它
    #    覆盖成当前远端值，一个本来该报的更新就被永久吞掉了。宁可这次什么都
    #    不判，并让 UI 显示 error。
    baseline_readable = True
    try:
        status.known_digest = (await db.get_app_meta(IMAGE_DIGEST_META_KEY) or "").strip()
    except Exception as exc:  # noqa: BLE001 - UI 面前绝不抛
        logger.exception("读取镜像指纹基线失败")
        baseline_readable = False
        status.error = f"读不到上次记录的镜像指纹（{_short_reason(exc)}）。"

    # 2) 远端指纹
    try:
        remote_digest = await fetch_remote_digest(repo, tag)
    except ImageUpdateError as exc:
        logger.warning("获取远端镜像指纹失败：%s", exc)
        status.error = str(exc) or "获取远端镜像指纹失败。"
        return status
    except Exception as exc:  # noqa: BLE001 - 兜底，任何意外都不许抛给 UI
        logger.exception("获取远端镜像指纹时出现未预期的错误")
        status.error = f"获取远端镜像指纹失败（{_short_reason(exc)}）。"
        return status

    status.remote_digest = remote_digest

    # 3) 判定
    if status.known_digest:
        status.update_available = remote_digest != status.known_digest
        return status

    # 首次运行：只建立基线，不报更新
    status.update_available = False
    if not baseline_readable:
        # 基线读不到 → 不知道真正记的是什么，不能瞎覆盖
        return status
    try:
        await remember_current_digest(remote_digest)
    except Exception as exc:  # noqa: BLE001
        logger.exception("写入镜像指纹基线失败")
        status.error = f"记不下镜像指纹基线（{_short_reason(exc)}），下次还会重新建基线。"
    else:
        logger.info(
            "已建立镜像指纹基线：%s (%s:%s)",
            remote_digest[:19],
            repo,
            tag,
        )
    return status


async def trigger_watchtower_update() -> tuple[bool, str]:
    """让 watchtower 去 `POST /v1/update`（拉新镜像 + 重建容器）。

    返回 ``(成功与否, 给主人看的简短说明)``，**不抛异常**。
    没配 token 时直接返回失败，不发一个注定 401 的请求。
    """
    token = (os.environ.get("WATCHTOWER_HTTP_API_TOKEN") or "").strip()
    if not token:
        return (
            False,
            "还没配 watchtower 的口令呢，主人先设好 WATCHTOWER_HTTP_API_TOKEN 嘛。",
        )

    base = (os.environ.get("WATCHTOWER_HTTP_API_URL") or "").strip() or WATCHTOWER_DEFAULT_URL
    url = base.rstrip("/") + WATCHTOWER_UPDATE_PATH
    # 注意：日志只打 _redact_url(url)，token 只在请求头里，绝不落日志。
    logger.info("请求 watchtower 更新镜像：%s", _redact_url(url))

    try:
        status_code, body = await asyncio.to_thread(_post_watchtower_update, url, token)
    except ImageUpdateError as exc:
        logger.warning("触发 watchtower 更新失败：%s", exc)
        return False, str(exc) or "没能叫醒 watchtower。"
    except Exception as exc:  # noqa: BLE001
        logger.exception("触发 watchtower 更新时出现未预期的错误")
        return False, f"请求 watchtower 时出错了（{_short_reason(exc)}）。"

    if 200 <= status_code < 300:
        return True, "已经吩咐 watchtower 去更新镜像了，主人稍等一会儿。"

    hint = _short_reason(body) if body else ""
    detail = f"HTTP {status_code}"
    if hint:
        detail = f"{detail}: {hint}"
    return False, f"watchtower 没答应呢（{detail}），主人看看它的日志嘛。"


def _post_watchtower_update(url: str, token: str) -> tuple[int, str]:
    """同步 POST（在 ``asyncio.to_thread`` 里跑）。"""
    request = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Length": "0",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with _urlopen(request) as response:
            body = response.read()
            code = getattr(response, "status", None) or response.getcode()
            return int(code), body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # HTTPError 也是个 readable response，body 里可能有 watchtower 的说明
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        return int(getattr(exc, "code", 0) or 0), body
    except urllib.error.URLError as exc:
        raise ImageUpdateError(
            f"连不上 watchtower（{_short_reason(exc.reason)}）。"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise ImageUpdateError(f"连不上 watchtower（{_short_reason(exc)}）。") from exc


# --------------------------------------------------------------------------- #
# 纯文本状态（与 services/safe_update.py:format_status 同风格：`字段: 值`）
# --------------------------------------------------------------------------- #


def format_image_status(status: ImageUpdateStatus) -> str:
    """给主人看的纯文本状态报告。

    字段名沿用项目既有的技术字段风格（半角冒号 + 空格），
    ``short_local`` / ``short_remote`` 各取前 12 字符，空值显示 ``-``。
    """
    known = status.known_digest[:12] if status.known_digest else "-"
    lines = [
        "镜像更新状态",
        "",
        f"运行环境: {'Docker 容器' if status.in_container else '非容器环境'}",
        f"镜像: {status.image}",
        f"本地版本: {status.short_local}",
        f"远端指纹: {status.short_remote}",
        f"已知指纹: {known}",
        f"有可用更新: {'是' if status.update_available else '否'}",
    ]
    if status.error:
        lines.append(f"错误: {status.error}")
    return "\n".join(lines)
