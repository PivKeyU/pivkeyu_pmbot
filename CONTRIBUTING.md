# 参与贡献

欢迎提交 Issue 和 Pull Request。

## 开始之前

- 报 Bug 时请写清部署方式（Docker 或源码）、复现步骤和相关日志。日志里的 Token 和 API Key 记得打码
- 较大的功能改动，建议先开 Issue 讨论方向

## 本地开发

```bash
git clone https://github.com/PivKeyU/pivkeyu_pmbot.git
cd pivkeyu_pmbot
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
pytest tests/
```

只想看 Web 面板界面时，不需要 Token：

```bash
python -m web_panel.preview
```

打开 `http://127.0.0.1:18100` 即可，数据都是假的，存在内存里。

## 提交前检查

- `pytest tests/` 全部通过
- 运行 `black` 和 `flake8`
- 新增用户可见文案放到 `utils/copy.py`，并遵守 [人设与语气](docs/persona.md)
- 新增命令加到 `utils/commands.py`，并更新 [命令参考](docs/commands.md)
- 新增环境变量同步更新 `config.py`、`config_meta.py`、`.env.example` 和 [配置参考](docs/configuration.md)

更多细节见 [开发指南](docs/development.md)。
