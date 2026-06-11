import asyncio
from dataclasses import dataclass
from pathlib import Path

from database import models as db


@dataclass
class GitUpdateStatus:
    branch: str
    head: str
    remote_head: str
    ahead: int
    behind: int
    dirty: bool

    @property
    def short_head(self) -> str:
        return self.head[:12] if self.head else "-"

    @property
    def short_remote_head(self) -> str:
        return self.remote_head[:12] if self.remote_head else "-"


class SafeUpdateError(Exception):
    pass


async def _git(repo_dir: Path, *args: str, check: bool = True) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo_dir),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if check and process.returncode != 0:
        raise SafeUpdateError((err or out or f"git {' '.join(args)} failed").strip())
    return out


async def current_branch(repo_dir: Path) -> str:
    try:
        branch = (await _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")).strip()
        return branch or "main"
    except Exception:
        return "main"


async def get_status(repo_dir: Path, fetch_remote: bool = True) -> GitUpdateStatus:
    branch = await current_branch(repo_dir)
    if fetch_remote:
        await _git(repo_dir, "fetch", "origin", branch)
    head = (await _git(repo_dir, "rev-parse", "HEAD")).strip()
    remote_ref = f"origin/{branch}"
    remote_head = (await _git(repo_dir, "rev-parse", remote_ref)).strip()
    counts_raw = (await _git(repo_dir, "rev-list", "--left-right", "--count", f"HEAD...{remote_ref}")).strip()
    counts = counts_raw.split()
    ahead = int(counts[0]) if len(counts) > 0 and counts[0].isdigit() else 0
    behind = int(counts[1]) if len(counts) > 1 and counts[1].isdigit() else 0
    dirty = bool((await _git(repo_dir, "status", "--porcelain")).strip())
    return GitUpdateStatus(
        branch=branch,
        head=head,
        remote_head=remote_head,
        ahead=ahead,
        behind=behind,
        dirty=dirty,
    )


async def apply_update(repo_dir: Path) -> GitUpdateStatus:
    status = await get_status(repo_dir, fetch_remote=True)
    if status.dirty:
        raise SafeUpdateError("检测到本地未提交改动，已拒绝自动更新。请先提交或清理本地改动。")
    if status.behind <= 0:
        return status
    if status.ahead > 0:
        raise SafeUpdateError("本地分支领先远端，已拒绝自动更新。请先手动处理分支差异。")

    await db.set_app_meta("last_update_rollback", status.head)
    await _git(repo_dir, "merge", "--ff-only", f"origin/{status.branch}")
    return await get_status(repo_dir, fetch_remote=False)


async def rollback_last_update(repo_dir: Path) -> str:
    rollback_commit = await db.get_app_meta("last_update_rollback")
    if not rollback_commit:
        raise SafeUpdateError("没有可用的回滚点。")

    status = await get_status(repo_dir, fetch_remote=False)
    if status.dirty:
        raise SafeUpdateError("检测到本地未提交改动，已拒绝回滚。请先处理本地改动。")

    await _git(repo_dir, "cat-file", "-e", f"{rollback_commit}^{{commit}}")
    await _git(repo_dir, "reset", "--hard", rollback_commit)
    return rollback_commit


def format_status(status: GitUpdateStatus, rollback_commit: str = "") -> str:
    return (
        "安全更新状态\n\n"
        f"分支: {status.branch}\n"
        f"本地: {status.short_head}\n"
        f"远端: {status.short_remote_head}\n"
        f"领先远端: {status.ahead}\n"
        f"落后远端: {status.behind}\n"
        f"工作区未提交改动: {'是' if status.dirty else '否'}\n"
        f"上次回滚点: {(rollback_commit or '-')[:12]}"
    )
