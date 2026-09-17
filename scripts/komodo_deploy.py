#!/usr/bin/env python3
"""手动触发一次 Komodo 全局自动更新（= 立刻把 libre_quant 上线）。

为什么需要它：komodo.librespaces.com 前面挂着 Cloudflare，机房 IP（GitHub runner）
会被下发 JS 挑战，CI 调不动 Komodo API —— 正常路径是「CI 出镜像 → Komodo 每 30 分钟
的 Global Auto Update 自动拉取重建」。这个脚本就是**不等那 30 分钟**的快捷方式。

机制说明（踩过的坑）：
  - 普通 `DeployStack` / `DeployStackIfChanged` **不会**因为 `:latest` 的 digest 变了
    而重建容器（Compose 比的是服务配置哈希，不是镜像 digest）；
  - 只有 `GlobalAutoUpdate` 这条路径是 digest 感知的，它会在 Compose Up 阶段
    做 `Container Recreate`；
  - 副作用：它会顺带检查你其它开了 auto_update 的 stack（它们本来也会在
    下一个 30 分钟刻度被检查，所以影响很小）。

用法::

    uv run python scripts/komodo_deploy.py             # 同步 compose + 触发 + 等结果
    uv run python scripts/komodo_deploy.py --no-sync   # 不同步 compose

凭据：仓库根目录 `.komodo.local`（已 git 忽略），三行：

    KOMODO_URL=https://komodo.librespaces.com
    KOMODO_API_KEY=K_...
    KOMODO_API_SECRET=S_...

注意：Komodo API 是根路径上的 RPC 风格（POST /read/GetStack、/execute/GlobalAutoUpdate），
且 Cloudflare 会拦 python-urllib 的 UA，所以这里显式伪装成 curl 的 UA。
另外 Komodo 的 update 日志会把 stack 环境变量明文打出来（含数据库密码），
本脚本默认过滤掉这些阶段，不回显。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STACK_ID = "6aab4422bfb6d8a625ac26ad"  # Komodo 里名为 quant 的 stack
CREDS = ROOT / ".komodo.local"
COMPOSE = ROOT / "docker-compose.yml"
CONTAINER = "libre_quant"

# 这些阶段的输出含明文密钥（DATABASE_URL / TAVILY_TOKEN），不打印
SECRET_STAGES = {"Write Environment File", "Compose Config"}


def load_creds() -> tuple[str, str, str]:
    if not CREDS.exists():
        sys.exit(f"缺少凭据文件 {CREDS}（三行：KOMODO_URL / KOMODO_API_KEY / KOMODO_API_SECRET）")
    kv = dict(
        line.split("=", 1)
        for line in CREDS.read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    )
    try:
        return kv["KOMODO_URL"].rstrip("/"), kv["KOMODO_API_KEY"], kv["KOMODO_API_SECRET"]
    except KeyError as e:  # pragma: no cover
        sys.exit(f"{CREDS} 缺字段: {e}")


class Komodo:
    def __init__(self, url: str, key: str, secret: str):
        self.url, self.key, self.secret = url, key, secret

    def api(self, op: str, body: dict | None = None, timeout: int = 300) -> dict:
        req = urllib.request.Request(
            f"{self.url}/{op}",
            data=json.dumps(body or {}).encode(),
            headers={
                "x-api-key": self.key,
                "x-api-secret": self.secret,
                "content-type": "application/json",
                # Cloudflare 按 UA 拦人：python-urllib 会被 403，curl 不会
                "user-agent": "curl/8.5.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def container(self) -> dict:
        servers = self.api("read/ListServers") or []
        sid = next((s["id"] for s in servers if "Mini" in str(s.get("name"))), servers[0]["id"])
        for c in self.api("read/ListDockerContainers", {"server": sid}) or []:
            if c.get("name") == CONTAINER:
                return c
        return {}

    def container_key(self) -> str:
        """只取能反映「容器有没有被重建」的字段（status 里带 Up X minutes，不能用）。"""
        c = self.container()
        return f"{c.get('created')}|{c.get('image_id')}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack-id", default=STACK_ID)
    ap.add_argument("--no-sync", action="store_true", help="不把仓库 compose 同步进 stack")
    ap.add_argument("--timeout", type=int, default=900, help="等待完成的秒数")
    args = ap.parse_args(argv)

    url, key, secret = load_creds()
    k = Komodo(url, key, secret)

    if not args.no_sync:
        stack = k.api("read/GetStack", {"stack": args.stack_id})
        want = COMPOSE.read_text()
        if (stack.get("config") or {}).get("file_contents") == want:
            print("compose 与 Komodo 一致，跳过同步")
        else:
            cfg = dict(stack["config"])
            cfg["file_contents"] = want
            k.api("write/UpdateStack", {"id": args.stack_id, "config": cfg})
            print(f"compose 已同步到 stack（{len(want)} 字节）")

    before = k.container_key()
    print("触发 GlobalAutoUpdate（digest 变了才会重建容器）…")
    resp = k.api("execute/GlobalAutoUpdate", {})
    if isinstance(resp, dict) and resp.get("error"):
        print(f"没能触发：{resp['error']}")
        print("（多半是上一次全局检查还在跑；等一两分钟再试，或直接等调度）")
        return 2

    deadline = time.time() + args.timeout
    upd = None
    while time.time() < deadline:
        time.sleep(8)
        ups = k.api("read/ListUpdates", {}).get("updates", [])
        cand = next((u for u in ups if u["operation"] == "GlobalAutoUpdate"), None)
        if not cand:
            continue
        upd = k.api("read/GetUpdate", {"id": cand["id"]})
        if upd.get("status") == "Complete":
            break

    if not upd or upd.get("status") != "Complete":
        print("超时：自动更新仍在执行，去 Komodo UI 看进度")
        return 1

    ok = upd.get("success")
    print(f"GlobalAutoUpdate 完成：success={ok}")
    for log in upd.get("logs") or []:
        stage = log.get("stage")
        if stage in SECRET_STAGES:
            print(f"--- [{stage}] （含密钥，已省略）")
            continue
        out = ((log.get("stdout") or "") + (log.get("stderr") or "")).strip()
        if out:
            print(f"--- [{stage}]\n{out[:2000]}")

    after_c = k.container()
    if k.container_key() == before:
        print(f"\n容器未重建 —— 镜像 digest 未变（当前 image_id={str(after_c.get('image_id'))[:20]}）")
    else:
        print(f"\n✅ 容器已重建，新镜像生效（{after_c.get('status')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
