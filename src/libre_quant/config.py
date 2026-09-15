"""集中配置（pydantic-settings）。

约定
----
* 仓库提供 ``.env.example`` 模板：``cp .env.example .env`` 后填写即可。
* ``.env`` 已进 ``.gitignore``，绝不提交。
* 所有配置项 = 同名环境变量（大写），``.env`` 与真实环境变量都可注入，
  环境变量优先。

字段速查
--------
``DATABASE_URL``
    PostgreSQL（家服务器中心库，docs/06 Phase 1a）。
    采集端（家服务器 cron）读写；分析端建议只读账号。
    ⚠️ 5432 不暴露公网，走 wireguard/tailscale 或 SSH 隧道。
``TAVILY_TOKEN``
    Tavily MCP 检索 token（可选，归因/预警用）。
``TRADING_FEE_RATE`` / ``TRADING_FEE_MIN``
    ETF 场内佣金：费率 + 单笔最低。用户实际券商口径：
    万 0.5 / 最低 0.1 元（≈免五）。回测的 ``COST_PER_SIDE`` 是更保守的
    佣金+滑点合并假设，两者用途不同（前者算实账，后者做策略压力）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 仓库根（``src/libre_quant/config.py`` 向上两级），``.env`` 按根解析，与 CWD 无关
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置。默认值只用于本地开发，生产值一律来自 ``.env``。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: PostgreSQL 中心库 DSN
    database_url: PostgresDsn = (
        "postgresql://libre_quant:libre_quant@localhost:5432/libre_quant"
    )

    #: Tavily MCP token（可选）
    tavily_token: str | None = None

    #: ETF 场内佣金费率（默认用户券商实际口径：万 0.5）
    trading_fee_rate: float = Field(default=0.00005)
    #: 单笔佣金最低（默认 0.1 元，≈免五）
    trading_fee_min: float = Field(default=0.1)


@lru_cache
def get_settings() -> Settings:
    """进程内单例。测试里用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()
