"""项目署名信息：作者、仓库地址与各处展示用的署名文案。

所有对外展示「制作者」的地方（/start、/help、/panel、启动日志）都从这里取，
改署名只需要动这一个文件。Web 面板的署名在 web_panel/static/js/app.js 的 ABOUT 常量里。
"""

PROJECT_NAME = 'pivkeyu_pmbot'
AUTHOR = '片刻月'
AUTHOR_URL = 'https://github.com/PivKeyU'
REPO_URL = 'https://github.com/PivKeyU/pivkeyu_pmbot'


def signature_plain() -> str:
    """纯文本署名（不带 parse_mode 的消息使用）。"""
    return f'本女仆由伟大的 {AUTHOR} 创造 · {REPO_URL}'


def signature_markdown() -> str:
    """Markdown（v1）署名：链接写成 [文字](地址)，避免仓库名里的下划线被当成斜体。"""
    return f'本女仆由伟大的 [{AUTHOR}]({AUTHOR_URL}) 创造 · [项目主页]({REPO_URL})'


def startup_banner() -> str:
    """启动日志里的署名横幅。"""
    return f'{PROJECT_NAME} · 作者 {AUTHOR} · {REPO_URL}'
