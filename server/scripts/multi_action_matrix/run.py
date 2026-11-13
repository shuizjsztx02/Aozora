"""多动作/多意图测试集执行器（M8-19b 建立，2026-11-13）。

用法::

    cd server
    python scripts/multi_action_matrix/run.py                 # 打测试实例
    AOZORA_BASE=http://127.0.0.1:8000/api/v1 python scripts/multi_action_matrix/run.py

三条判据（与 ``cases.json`` 的字段对应）：

- **动作理解**：每个 ``expect`` 工具组至少被调用其一（``missing`` 为空）；
- **工具激活**：全程不出现 ``tool_unavailable``；路由域包含 ``domains``；
- **token 消耗**：``done`` 帧的轮合计（分步明细在运营后台的 ``usage`` 事件）。

每条用例使用**新 guest**（隔离账号、新账本、固定种子分类/账户）。auth 端点
按 IP 限流 10 次/60 秒，脚本对 429 退避重试并保持条间节奏。
输出 ``results.jsonl``（逐条）与控制台汇总。

**注意**：删除/编辑类用例需要预置历史数据才有意义；本执行器不预置，因此这类
用例的「未执行」可能是「账本里确实没有那笔」，判读时结合回复与 ``writes``。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BASE = os.getenv("AOZORA_BASE", "http://116.62.196.255/aozora-api/api/v1")
CASES = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]

CATEGORIES = [
    ("expense", "餐饮"), ("expense", "交通"), ("expense", "其他"),
    ("expense", "宠物"), ("expense", "住房"), ("expense", "购物"),
    ("income", "收转账"), ("income", "工资"), ("income", "红包"),
]


async def _post_with_retry(c: httpx.AsyncClient, url: str, **kwargs):
    for _ in range(8):
        r = await c.post(url, **kwargs)
        if r.status_code != 429:
            r.raise_for_status()
            return r
        await asyncio.sleep(12)
    r.raise_for_status()
    return r


async def run_case(c: httpx.AsyncClient, case: dict) -> dict:
    dev = f"ma-probe-{uuid.uuid4().hex[:8]}"
    r = await _post_with_retry(c, f"{BASE}/auth/guest", json={
        "device_id": dev, "timezone": "Asia/Shanghai",
    })
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    await _post_with_retry(c, f"{BASE}/consent", headers=h, json={
        "device_id": dev, "policy_version": "v1",
        "consented_at": "2026-11-13T09:00:00+08:00",
    })
    r = await _post_with_retry(c, f"{BASE}/books", headers=h,
                               json={"name": "多动作探针"})
    book = r.json()["id"]
    for kind, name in CATEGORIES:
        await _post_with_retry(c, f"{BASE}/categories", headers=h, json={
            "kind": kind, "name": name, "book_id": None,
        })
    for name, acc_type in (("支付宝", "e_wallet"), ("现金", "cash")):
        await _post_with_retry(c, f"{BASE}/accounts", headers=h, json={
            "name": name, "account_type": acc_type,
        })

    routing = None
    levels: list[str] = []
    tool_events: list[dict] = []
    done: dict = {}
    event = None
    async with c.stream("POST", f"{BASE}/ai/agent", headers=h, json={
        "input": case["text"], "book_id": book, "proposal_contract_version": 2,
    }) as resp:
        status = resp.status_code
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "phase":
                    if data.get("phase") == "routing":
                        routing = {"domains": data.get("domains"),
                                   "matched": data.get("matched")}
                    elif data.get("phase") == "thinking" and "reasoning" in data:
                        levels.append(data["reasoning"])
                elif event == "tool_result":
                    tool_events.append({
                        "tool": data.get("tool"), "status": data.get("status"),
                        "code": data.get("code"),
                    })
                elif event == "done":
                    done = data

    called = [t["tool"] for t in tool_events]
    errors = [t for t in tool_events if t["status"] != "ok"]
    unavailable = [t for t in errors if t.get("code") == "tool_unavailable"]

    missing: list[list[str]] = []
    for group in case["expect"]:
        if not any(any(name == called_name or called_name.startswith(name)
                       for called_name in called) for name in group):
            missing.append(group)
    domains_ok = (
        all(d in (routing.get("domains") or []) for d in case["domains"])
        if case["domains"] and routing is not None else True
    )
    write_forbidden_violation = case.get("forbid_write") and done.get("writes", 0) > 0

    return {
        "id": case["id"], "text": case["text"], "note": case["note"],
        "status": status,
        "routing": routing, "levels": levels,
        "called": called, "errors": errors,
        "unavailable": len(unavailable), "missing": missing,
        "domains_ok": domains_ok,
        "write_forbidden_violation": bool(write_forbidden_violation),
        "steps": done.get("steps"), "tool_calls": done.get("tool_calls"),
        "writes": done.get("writes"),
        "usage": done.get("usage"),
    }


async def main() -> None:
    results = []
    async with httpx.AsyncClient(timeout=300) as c:
        for case in CASES:
            try:
                res = await run_case(c, case)
            except Exception as exc:  # noqa: BLE001 — 单条失败不中断整轮
                res = {"id": case["id"], "text": case["text"], "error": repr(exc)}
            results.append(res)
            print(json.dumps(res, ensure_ascii=False)[:500], flush=True)
            await asyncio.sleep(7)   # auth 限流：10 次 / 60 秒 / IP

    with (HERE / "results.jsonl").open("w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    ok_actions = sum(1 for r in results if not r.get("missing") and not r.get("error"))
    ok_domains = sum(1 for r in results if r.get("domains_ok"))
    no_unavailable = sum(1 for r in results if not r.get("unavailable"))
    total_prompt = sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in results)
    total_completion = sum((r.get("usage") or {}).get("completion_tokens", 0) for r in results)
    print("\n=== 汇总 ===")
    print(f"动作理解（期望工具全部出现）：{ok_actions}/{len(results)}")
    print(f"路由域正确：{ok_domains}/{len(results)}")
    print(f"无 tool_unavailable：{no_unavailable}/{len(results)}")
    print(f"token 合计：prompt {total_prompt} / completion {total_completion}")


if __name__ == "__main__":
    asyncio.run(main())
