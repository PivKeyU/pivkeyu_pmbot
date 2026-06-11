from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from database import models as db


@dataclass
class SpamKeywordResult:
    enabled: bool
    auto_block: bool
    hits: list[str]

    @property
    def matched(self) -> bool:
        return bool(self.hits)


def _keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    normalized_text = (text or "").lower()
    if not normalized_text:
        return []

    hits = []
    for keyword in keywords:
        keyword_text = (keyword or "").strip()
        if not keyword_text:
            continue
        if keyword_text.lower() in normalized_text:
            hits.append(keyword_text)
    return hits


async def check_message_text(text: str) -> SpamKeywordResult:
    settings = await db.get_spam_keyword_filter_settings()
    if not settings["enabled"]:
        return SpamKeywordResult(False, settings["auto_block"], [])

    return SpamKeywordResult(
        enabled=True,
        auto_block=settings["auto_block"],
        hits=_keyword_hits(text, settings["keywords"]),
    )


async def format_settings() -> str:
    settings = await db.get_spam_keyword_filter_settings()
    keywords = settings["keywords"]
    lines = [
        "关键词广告拦截",
        "",
        f"当前状态: {'已启用' if settings['enabled'] else '已关闭'}",
        f"命中后自动拉黑: {'是' if settings['auto_block'] else '否'}",
        f"关键词数量: {len(keywords)}",
    ]
    if keywords:
        lines.extend(["", "关键词："])
        lines.extend(f"- {keyword}" for keyword in keywords[:60])
        if len(keywords) > 60:
            lines.append(f"... 还有 {len(keywords) - 60} 个")
    else:
        lines.extend(["", "当前还没有关键词。"])
    return "\n".join(lines)
