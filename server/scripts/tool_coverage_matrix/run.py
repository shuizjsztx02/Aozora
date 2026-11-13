"""全工具覆盖 + 多动作测试集执行器（92 条）。

**在 api 容器内运行**（需要 ``src`` 与内部 DB/provider 配置）::

    docker cp server/scripts/tool_coverage_matrix aozora-api-test:/tmp/tcm
    docker exec aozora-api-test python /tmp/tcm/run.py

以 ``cases.json`` 的 ``user_id``（真实测试账户）自签 token，不消耗 guest 限额。
前置：找到/新建「自动化测试账本」，建分类/账户、记两笔历史交易——后续
list/update/delete/预算类用例依赖它们。

判据：
- **动作理解**：``expect`` 的每个工具组至少命中其一；
- **工具激活**：不出现不在 ``allow_error`` 里的错误码；
- **token**：``done`` 帧的轮合计（分步明细在运营后台 ``usage`` 事件）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from src.modules.auth.utils import create_access_token

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8000/api/v1"
BOOK_NAME = "自动化测试账本"

CASES = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
USER_ID: str = CASES["user_id"]


async def setup(c: httpx.AsyncClient, h: dict) -> dict:
    """返回 ``{book_id, categories: {...}, accounts: {...}}``，全部容错。"""
    state: dict = {"categories": {}, "accounts": {}}
    r = await c.get(f"{BASE}/books", headers=h)
    books = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    book = next((b for b in books if b.get("name") == BOOK_NAME), None)
    if book is None:
        r = await c.post(f"{BASE}/books", headers=h, json={"name": BOOK_NAME})
        book = r.json()
    state["book_id"] = book["id"]

    r = await c.get(f"{BASE}/categories", headers=h)
    existing = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    for kind, name in (
        ("expense", "餐饮"), ("expense", "交通"), ("expense", "其他"),
        ("expense", "宠物"), ("income", "收转账"),
    ):
        hit = next((x for x in existing if x.get("name") == name
                    and x.get("kind") == kind), None)
        if hit is None:
            r = await c.post(f"{BASE}/categories", headers=h, json={
                "kind": kind, "name": name, "book_id": None,
            })
            hit = r.json() if r.status_code in (200, 201) else None
        if hit:
            state["categories"][name] = hit["id"]

    r = await c.get(f"{BASE}/accounts", headers=h)
    existing_accounts = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    for name, acc_type in (("支付宝", "e_wallet"), ("现金", "cash")):
        hit = next((x for x in existing_accounts if x.get("name") == name), None)
        if hit is None:
            r = await c.post(f"{BASE}/accounts", headers=h, json={
                "name": name, "account_type": acc_type,
            })
            hit = r.json() if r.status_code in (200, 201) else None
        if hit:
            state["accounts"][name] = hit["id"]

    # 两笔历史交易（今天午饭 30、昨天奶茶 18）
    today = date.today()
    now = datetime.now(timezone.utc)
    for amount, note, biz in (
        (3000, "午饭", today),
        (800, "早餐", today),
        (1800, "奶茶", today - timedelta(days=1)),
    ):
        cat = state["categories"].get("餐饮")
        if not cat:
            continue
        await c.post(f"{BASE}/transactions", headers=h, json={
            "book_id": state["book_id"], "type": "expense",
            "amount_cents": amount, "category_id": cat,
            "account_id": state["accounts"].get("支付宝"),
            "note": note, "biz_date": biz.isoformat(),
            "occurred_at": now.isoformat(),
        })
    return state


async def run_case(c: httpx.AsyncClient, h: dict, book_id: str, case: dict) -> dict:
    routing = None
    levels: list[str] = []
    tool_events: list[dict] = []
    tool_call_names: list[str] = []
    proposals: list[dict] = []
    done: dict = {}
    error_frame: dict = {}
    event = None
    async with c.stream("POST", f"{BASE}/ai/agent", headers=h, json={
        "input": case["text"], "book_id": book_id, "proposal_contract_version": 2,
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
                elif event == "tool_call":
                    # 执行前帧：读工具与写工具（含产出提案的）都发这一帧。
                    name = data.get("tool")
                    if name:
                        tool_call_names.append(name)
                elif event == "tool_result":
                    tool_events.append({
                        "tool": data.get("tool"), "status": data.get("status"),
                        "code": data.get("code"),
                    })
                elif event == "confirm":
                    prop = data.get("proposal")
                    if isinstance(prop, dict):
                        proposals.append(prop)
                elif event == "done":
                    done = data
                elif event == "error":
                    error_frame = data

    # 写工具走提案路径（只有 confirm 帧、没有 tool_result），因此「激活」以
    # tool_call 帧为准；模拟用户点确认，让 A 阶段创建的数据真正落库。
    executed = []
    for prop in proposals:
        r = await c.post(f"{BASE}/ai/execute", headers=h, json={
            "proposal": prop, "book_id": book_id,
            "confirmation_token": "allow-once",
        })
        executed.append({"ok": r.status_code < 300, "status": r.status_code,
                         "detail": (r.text or "")[:160]})
    called = list(dict.fromkeys(tool_call_names))
    allow = set(case.get("allow_error") or [])
    unexpected = [
        t for t in tool_events
        if t["status"] != "ok" and t.get("code") not in allow
    ]
    missing = [
        group for group in case["expect"]
        if not any(
            any(name == called_name or called_name.startswith(name) for name in group)
            for called_name in called
        )
    ]
    domains_ok = (
        all(d in (routing.get("domains") or []) for d in case["domains"])
        if case["domains"] and routing is not None else True
    )
    forbid_violation = bool(case.get("forbid_write")) and done.get("writes", 0) > 0

    return {
        "id": case["id"], "text": case["text"], "note": case["note"],
        "status": status, "routing": routing, "levels": levels,
        "called": called, "unexpected_errors": unexpected,
        "proposals": len(proposals), "executed": executed,
        "missing": missing, "domains_ok": domains_ok,
        "forbid_violation": forbid_violation,
        "steps": done.get("steps"), "tool_calls": done.get("tool_calls"),
        "writes": done.get("writes"), "usage": done.get("usage"),
        "error_frame": error_frame or None,
    }


async def main() -> None:
    token = create_access_token(USER_ID)
    h = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=300) as c:
        state = await setup(c, h)
        print("setup:", {"book_id": state["book_id"],
                         "categories": list(state["categories"]),
                         "accounts": list(state["accounts"])}, flush=True)
        results = []
        for case in CASES["cases"]:
            try:
                res = await run_case(c, h, state["book_id"], case)
            except Exception as exc:  # noqa: BLE001 — 单条失败不中断
                res = {"id": case["id"], "text": case["text"], "error": repr(exc)}
            results.append(res)
            print(json.dumps(res, ensure_ascii=False)[:520], flush=True)
            await asyncio.sleep(1)

    out = Path("/tmp/tcm_results.jsonl")  # 容器内 /tmp 可写（脚本目录属 root）
    with out.open("w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if not r.get("missing") and not r.get("error"))
    ok_domains = sum(1 for r in results if r.get("domains_ok"))
    clean = sum(1 for r in results
                if not r.get("unexpected_errors") and not r.get("error"))
    no_violation = sum(1 for r in results if not r.get("forbid_violation"))
    prompt = sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in results)
    completion = sum((r.get("usage") or {}).get("completion_tokens", 0) for r in results)
    print("\n=== 汇总 ===")
    print(f"动作理解（期望工具全部出现）：{ok}/{len(results)}")
    print(f"路由域正确：{ok_domains}/{len(results)}")
    print(f"无意外工具错误：{clean}/{len(results)}")
    print(f"负例无写库：{no_violation}/{len(results)}")
    print(f"token 合计：prompt {prompt} / completion {completion}")
    print(f"结果写入 {out}")


if __name__ == "__main__":
    asyncio.run(main())
