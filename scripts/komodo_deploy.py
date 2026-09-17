#!/usr/bin/env python3
"""手动触发 Komodo 部署（本机执行，不走 GitHub runner）。

为什么需要它：komodo.librespaces.com 前面挂着 Cloudflare，机房 IP（GitHub runner）
会被下发 JS 挑战，CI 调不动 Komodo API；所以正常路径是「CI 出镜像 → Komodo 侧
Procedure 每 5 分钟拉 latest 自动重建」。这个脚本是**立刻上线**的快捷方式，
顺便把仓库里的 docker-compose.yml 同步进 stack（避免两边漂移）。

用法::

    uv run python scripts/komodo_deploy.py             # 同步 compose + 部署 + 等结果
    uv run python scripts/komodo_deploy.py --no-sync   # 只部署，不同步 compose

凭据：仓库根目录 `.komodo.local`（已 git 忽略），三行：

    KOMODO_URL=https://komodo.librespaces.com
    KOMODO_API_KEY=K_...
    KOMODO_API_SECRET=S_...

注意：Komodo API 是根路径上的 RPC 风格（POST /read/GetStack、/execute/DeployStack），
且 Cloudflare 会拦 python-urllib 的 UA，所以这里显式伪装成 curl 的 UA。
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack-id", default=STACK_ID)
    ap.add_argument("--no-sync", action="store_true", help="不把仓库 compose 同步进 stack")
    ap.add_argument("--timeout", type=int, default=600, help="等待部署完成的秒数")
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

    before = {c["name"]: c.get("created") for c in _containers(k)}
    print("触发 DeployStack …")
    k.api("execute/DeployStack", {"stack": args.stack_id})

    upd_id = None
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(8)
        ups = k.api("read/ListUpdates", {}).get("updates", [])
        cand = next((u for u in ups if u["operation"] == "DeployStack"
                     and (u.get("target") or {}).get("id") == args.stack_id), None)
        if not cand:
            continue
        upd_id = cand["id"]
        u = k.api("read/GetUpdate", {"id": upd_id})
        if u.get("status") == "Complete":
            print(f"部署{'成功' if u.get('success') else '失败'}")
            for log in u.get("logs") or []:
                out = (log.get("stdout") or "").strip()
                err = (log.get("stderr") or "").strip()
                if out or err:
                    print(f"--- [{log.get('stage')}]")
                    if out:
                        print(out)
                    if err:
                        print("STDERR:", err)
            if not u.get("success"):
                return 1
            break
    else:
        print("超时：部署仍在执行，去 Komodo UI 看进度")
        return 1

    after = {c["name"]: c.get("created") for c in _containers(k)}
    if before.get("libre_quant") == after.get("libre_quant"):
        print("容器未重建（镜像 digest 未变）—— 已是最新")
    else:
        print("容器已重建，新镜像生效")
    return 0


def _containers(k: Komodo) -> list[dict]:
    """拿 Mini-Ubuntu 上的容器列表（只看名字/created 做前后对比）。"""
    servers = k.api("read/ListServers", {})
    if isinstance(servers, list) and servers:
        sid = next((s["id"] for s in servers if "Mini" in str(s.get("name"))), servers[0]["id"])
        got = k.api("read/ListDockerContainers", {"server": sid})
        if isinstance(got, list):
            return got
    return []


if __name__ == "__main__":
    raise SystemExit(main())
