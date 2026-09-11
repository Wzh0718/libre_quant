"""Tavily 检索（经 MCP Streamable HTTP）。

背景与实测结论（2026-09-11）
----------------------------
* DSH 里配的 ``https://tavily.ivanli.cc/mcp`` **本身是好的**，token 也有效。
* 但 ``web-search-deepseek`` 插件把 baseURL 拼成 ``/mcp/messages`` 去调
  **Anthropic 兼容的 Messages API**，而这台服务器只提供 **MCP 协议** ——
  **协议不匹配**，改 URL 治不好。
* 该主机上 ``/v1/messages``、``/messages``、``/sse``、``/mcp/sse`` 全部 404，
  唯一入口是 ``POST /mcp``（Streamable HTTP，SSE 响应）。

正确用法（本模块实现的就是这个）::

    POST https://tavily.ivanli.cc/mcp
    Authorization: Bearer <token>
    Content-Type: application/json
    Accept: application/json, text/event-stream

会话流程：``initialize`` → ``notifications/initialized`` → ``tools/call``。
响应是 ``text/event-stream``，需要解析 ``data:`` 行。

可用工具：``tavily_search`` / ``tavily_extract`` / ``tavily_crawl`` /
``tavily_map`` / ``tavily_research``。

⚠️ 关于因子价值：我们已用数据证明「连 COHR/LITE 这种业务直接对标、时间明确
领先的结构化数据，对 515880 日线收益的预测力都是 0」。新闻比它更噪、更滞后、
更难结构化。**因此本模块的正确用途是「事件风险预警 / 背景判断 / 归因解释」，
不是「预测收益」** —— 要用作因子，必须先过 gap/intra + 多 horizon 检验。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

MCP_URL = "https://tavily.ivanli.cc/mcp"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


class TavilyError(RuntimeError):
    """MCP 会话或工具调用失败。"""


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    content: str
    score: float | None = None


@dataclass(slots=True)
class SearchResult:
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] | None = None


class TavilyMCP:
    """极简 MCP Streamable HTTP 客户端（同步，仅依赖 requests）。"""

    def __init__(
        self,
        token: str | None = None,
        *,
        url: str = MCP_URL,
        timeout: float = 60.0,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.token = token or os.environ.get("TAVILY_TOKEN") or os.environ.get(
            "TAVILY_API_KEY"
        )
        if not self.token:
            raise TavilyError(
                "缺少 token：传入 token= 或设置环境变量 TAVILY_TOKEN"
            )
        self._session_id: str | None = None
        self._seq = 0
        self._sess = requests.Session()
        self._sess.headers.update({"User-Agent": _UA})
        self._sess.trust_env = False  # 沙箱代理对本域名不稳定

    # -- 内部 ---------------------------------------------------------------
    @property
    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["mcp-session-id"] = self._session_id
        return h

    def _post(self, payload: dict[str, Any], *, expect_reply: bool = True):
        resp = self._sess.post(
            self.url, headers=self._headers, json=payload, timeout=self.timeout
        )
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if resp.status_code >= 400:
            raise TavilyError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if not expect_reply:
            return None
        # 必须显式按 UTF-8 解码：响应头未声明 charset，requests 会退化成 latin-1
        body = resp.content.decode("utf-8", errors="replace")
        return self._parse_sse(body)

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any]:
        """从 text/event-stream 里取出 JSON-RPC 响应。

        SSE 规范允许一个事件的 data 跨多行，需按 ``\\n`` 拼接后再解析 ——
        检索结果很长时服务端确实会这么切。
        """
        events: list[str] = []
        buf: list[str] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
            elif line.strip() == "" and buf:
                events.append("\n".join(buf))
                buf = []
        if buf:
            events.append("\n".join(buf))

        for ev in events:
            try:
                return json.loads(ev)
            except json.JSONDecodeError:
                continue
        # 少数实现直接返回 application/json
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise TavilyError(f"无法解析响应: {text[:200]}") from exc

    def _rpc(self, method: str, params: dict | None = None) -> dict[str, Any]:
        self._seq += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._seq,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        data = self._post(payload)
        if "error" in data:
            raise TavilyError(f"{method} 失败: {data['error']}")
        return data.get("result", {})

    # -- 公开接口 -----------------------------------------------------------
    def connect(self) -> dict[str, Any]:
        """握手：initialize + notifications/initialized。返回 serverInfo。"""
        res = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "quant-etf", "version": "0.1.0"},
            },
        )
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_reply=False,
        )
        return res.get("serverInfo", {})

    def list_tools(self) -> list[str]:
        return [t["name"] for t in self._rpc("tools/list").get("tools", [])]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        res = self._rpc(
            "tools/call", {"name": name, "arguments": arguments}
        )
        if res.get("isError"):
            raise TavilyError(f"{name} 返回错误: {res}")
        for c in res.get("content", []):
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except json.JSONDecodeError:
                    return c["text"]
        return res

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        depth: str = "basic",
        topic: str | None = None,
        days: int | None = None,
    ) -> SearchResult:
        """检索。``days`` 限最近 N 天（新闻场景常用）。"""
        args: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "search_depth": depth,
        }
        if topic:
            args["topic"] = topic
        if days:
            args["days"] = days

        data = self.call("tavily_search", args)
        hits = [
            SearchHit(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
                score=r.get("score"),
            )
            for r in (data.get("results", []) if isinstance(data, dict) else [])
        ]
        return SearchResult(query=query, hits=hits, raw=data if isinstance(data, dict) else None)


def _cli() -> int:
    import sys

    q = " ".join(sys.argv[1:]) or "光模块 800G 1.6T 最新进展"
    c = TavilyMCP()
    print(f"serverInfo: {c.connect()}")
    print(f"tools: {c.list_tools()}")
    print(f"\n检索: {q}\n" + "-" * 70)
    for h in c.search(q, max_results=5).hits:
        print(f"• {h.title}")
        print(f"  {h.url}")
        print(f"  {h.content[:160]}...")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
