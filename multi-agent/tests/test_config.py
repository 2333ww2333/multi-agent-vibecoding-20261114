"""配置层单测：YAML、环境变量优先级、路径解析、key 不落配置。"""

from __future__ import annotations

import json

from agent.config import Settings


def _write_yaml(root, text: str) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "default.yaml").write_text(text, encoding="utf-8")


def test_defaults_without_yaml(tmp_path):
    settings = Settings.load(root=tmp_path)
    assert settings.llm.provider == "deepseek"
    assert settings.llm.model == "deepseek-flash"
    assert settings.llm.effective_base_url() == "https://api.deepseek.com"
    assert settings.limits.max_turns_per_task == 40
    assert settings.root == tmp_path


def test_yaml_is_loaded(tmp_path):
    _write_yaml(tmp_path, """
llm:
  model: my-model
  max_tokens: 123
limits:
  max_turns_per_task: 7
app:
  log_level: warn
""")
    settings = Settings.load(root=tmp_path)
    assert settings.llm.model == "my-model"
    assert settings.llm.max_tokens == 123
    assert settings.limits.max_turns_per_task == 7
    assert settings.app.log_level == "WARN"      # 校验器会归一化


def test_env_overrides_yaml_and_ma_wins(tmp_path, monkeypatch):
    _write_yaml(tmp_path, "llm:\n  model: from-yaml\n")
    monkeypatch.setenv("DEEPSEEK_MODEL", "from-deepseek")
    assert Settings.load(root=tmp_path).llm.model == "from-deepseek"

    monkeypatch.setenv("MA_MODEL", "from-ma")
    assert Settings.load(root=tmp_path).llm.model == "from-ma"


def test_env_casts_and_bad_values_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("MA_MAX_TOKENS", "512")
    monkeypatch.setenv("MA_DAILY_TOKEN_BUDGET", "1000")
    settings = Settings.load(root=tmp_path)
    assert settings.llm.max_tokens == 512
    assert settings.limits.daily_token_budget == 1000

    monkeypatch.setenv("MA_MAX_TOKENS", "not-a-number")
    assert Settings.load(root=tmp_path).llm.max_tokens == 8000   # 回落默认值


def test_base_url_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://gateway.example.com/v1")
    assert Settings.load(root=tmp_path).llm.effective_base_url() == \
        "https://gateway.example.com/v1"


def test_offline_env_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("MA_OFFLINE", "1")
    assert Settings.load(root=tmp_path).llm.offline is True
    monkeypatch.setenv("MA_OFFLINE", "0")
    assert Settings.load(root=tmp_path).llm.offline is False


def test_paths_resolve_under_root(tmp_path):
    settings = Settings.load(root=tmp_path)
    assert settings.path("memory_dir") == tmp_path / "memory"
    assert settings.path("audit_file") == tmp_path / ".team" / "audit.jsonl"
    settings.ensure_dirs()
    assert (tmp_path / "memory").is_dir()
    assert (tmp_path / ".team").is_dir()


def test_api_key_never_enters_settings_dump(tmp_path, monkeypatch):
    secret = "sk-this-must-not-leak"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    settings = Settings.load(root=tmp_path)
    assert settings.llm.api_key() == secret
    described = settings.describe()
    dumped = json.dumps(described, ensure_ascii=False)
    assert secret not in dumped
    assert described["llm"]["api_key_set"] is True
    assert "api_key" not in settings.llm.model_dump()
