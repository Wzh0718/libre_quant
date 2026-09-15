"""config 离线自测：默认值、环境变量覆盖、.env 解析。

不依赖网络与数据库。
"""

from __future__ import annotations

from pathlib import Path

from libre_quant.config import PROJECT_ROOT, Settings, get_settings


def test_defaults_without_env():
    s = Settings(_env_file=None)  # 显式忽略 .env，只看进程环境
    assert str(s.database_url).startswith("postgresql://")
    assert s.tavily_token is None


def test_env_var_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://reader:pw@10.0.0.2:5432/quant")
    monkeypatch.setenv("TAVILY_TOKEN", "tok-123")
    s = Settings(_env_file=None)
    # PostgresDsn 是 MultiHostUrl：多主机形态，用 hosts/整体串断言
    assert s.database_url.hosts()[0]["host"] == "10.0.0.2"
    assert s.database_url.path == "/quant"
    assert s.tavily_token == "tok-123"


def test_env_file_example_is_wellformed():
    """模板必须可被 Settings 解析（CHANGE_ME 占位不破坏 DSN 结构）。"""
    example = PROJECT_ROOT / ".env.example"
    assert example.exists()
    s = Settings(_env_file=example)
    assert s.database_url.scheme in {"postgres", "postgresql"}


def test_get_settings_singleton():
    a, b = get_settings(), get_settings()
    assert a is b
    get_settings.cache_clear()
