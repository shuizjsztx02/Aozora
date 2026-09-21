"""T-M7 全域名 AI 工具执行测试集 —— 服务端执行器。

用法::

    cd server
    python scripts/ai_tool_matrix/run.py                        # 全量（约 25 分钟）
    python scripts/ai_tool_matrix/run.py --domain budget        # 只跑某域（可多次）
    python scripts/ai_tool_matrix/run.py --layer boundary       # 只跑某一层
    python scripts/ai_tool_matrix/run.py --dry-run              # 只做前置 + 离线路由核验

    # 打真实服务器（A 机测试实例）
    AOZORA_BASE=http://116.62.196.255/aozora-api/api/v1 python scripts/ai_tool_matrix/run.py

判据（每个用例可同时命中多条）：:

  hit       期望工具出现在本轮的工具调用里（``expect`` 任一命中即算）
  also      附加断言（如记账后应调 ``device_widget_refresh``）
  landing   写操作的落库形态：
              landed     服务端**真的执行了**（``executions`` 非空）。只用于记账快
                         路径这类「单发、无前置读取」的用例
              produced   落库或出确认卡**二者有其一**。绝大多数写的正确判据：
                         落库与否取决于 ``confirm_reason``（``step >= 2`` → 强制确认），
                         而「第几步产出提案」是模型行为，不是用例的固定属性
              proposal   只挂起（设备 op 与高风险 op 的**预期**形态）
              forbidden  两条路都不许有（负例：一条写路径都不该被走）
              na         不判

  注意 ``done.writes`` **不是落库数**——它是「写工具的调用次数（含只产出提案的）」
  （``agent.py``：「其中 N 项写操作已生成待确认的提案」）。把它当落库用会把设备 op
  一律误判成「已执行」。

输出：``report.json``（逐条明细）+ 控制台汇总。

**顺序敏感**：用例数组顺序即执行顺序（同域先建后改）。不要并行、不要打乱。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus.json"
REPORT = HERE / "report.json"

BASE = os.environ.get("AOZORA_BASE", "http://127.0.0.1:8000/api/v1")
TZ = "Asia/Shanghai"
CN = timezone(timedelta(hours=8))

#: 设备 op：服务端只挂起（``tools.DEVICE_EXECUTED_OPS``）。
DEVICE_OPS = {"persona_switch", "consent_record", "sync_trigger", "reminder_set"}
#: 高风险 op：必须过确认卡（``tools.HIGH_RISK_OPS``）。
HIGH_RISK_OPS = {"account_deletion_request"}


def _write_tool_names() -> frozenset[str]:
    """注册表里的**写工具**名（负例判据用：它们一个都不该被调）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.modules.ai.tools import TOOLS
    return frozenset(t.name for t in TOOLS if t.kind == "write")


WRITE_TOOLS = _write_tool_names()


# ---------------------------------------------------------------------------
# 离线部分：路由核验（不花模型调用）
# ---------------------------------------------------------------------------

def offline_seed_check(corpus: dict) -> list[str]:
    """核验「改 / 删 / 归档 / 切换」用例的目标对象**来自播种**，而不是更早的 create 用例。

    这是本仓库第四类同源坑的守卫（2026-09-21 实测踩到）：服务端 ``confirm_reason``
    规定「先读再写」（``step >= 2``）只出确认卡、**不落库**——create 用例因此可能
    没真的建出对象，后续用例就会答「找不到 X」，**看起来像模型缺陷，实际是环境**。
    第一版语料这么误判了 5 条（`ACC-UPD-01` / `ACC-ARCH-01` / `SAV-UPD-01/02` /
    `SAV-ARCH-01`），全是模型答对了（对象不存在→澄清）却被记成失败。

    判据：用例 ``expect`` 里含 ``*_update / *_archive / *_delete / *_switch`` 时，
    它的文本必须至少命中一个**播种对象名**。命中不了就是「依赖更早的 create」。
    """
    spec = corpus["seed"]
    names: set[str] = set()
    for key in ("categories", "accounts", "personas", "savings", "scheduled", "books"):
        names |= {o["name"] for o in spec.get(key, [])}
    names |= {t.get("note", "") for t in spec.get("transactions", []) if t.get("note")}
    names |= {b["name"] for b in spec.get("books", [])} | {spec["book"]}
    names |= {b.get("categoryName", "") for b in spec.get("budgets", [])}
    names.discard("")

    MODIFY = ("_update", "_archive", "_delete", "_switch")
    mods = [c for c in corpus["cases"] if any(t.endswith(MODIFY) for t in c["expect"])]

    bad = []
    renamed: dict[str, str] = {}          # 被改名掉的播种名 → 改名用例 id
    for c in mods:
        if "改名" not in c["text"]:
            continue
        for n in names:
            if n in c["text"]:
                renamed[n] = c["id"]

    for c in mods:
        matched = [n for n in names if n in c["text"]]
        if not matched:
            # 一个播种名都点不到 = 目标来自更早的 create 用例（它可能只出确认卡、不落库）
            bad.append(c["id"])
            continue
        # **改名链**：目标名已被更早的用例改掉 → 必然「找不到 X」。
        # 本轮实测：`CAT-UPD-01` 把「宠物」改名成「猫主子」后，`CAT-ARCH-01` 再点名
        # 「宠物」就找不到了；账本同理。归档/删除要用**专属对象**，不与改名用例争用。
        for n in matched:
            src = renamed.get(n)
            if src and src != c["id"]:
                bad.append(f"{c['id']}(目标「{n}」已被 {src} 改名)")
    return bad


def offline_route_check(corpus: dict) -> list[dict]:
    """对每条用例跑 ``route_domains``，看期望工具的域是否进了注入面。

    **漏注入**（期望域不在注入集里）= 能力消失，是本测试集最该早发现的一类问题；
    它在真实会话里是静默的（模型只能说「我做不了」），离线可确定性量出。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.modules.ai.routing import route_domains
    from src.modules.ai.tools import TOOLS

    domain_of = {t.name: t.domain for t in TOOLS}
    out = []
    for case in corpus["cases"]:
        d = route_domains(case["text"])
        wanted = {domain_of[t] for t in case["expect"] if t in domain_of}
        missing = sorted(wanted - set(d.domains))
        out.append({
            "id": case["id"],
            "text": case["text"],
            "matched": d.matched,
            "signals": list(d.signals),
            "domains": list(d.domains),
            "wanted_domains": sorted(wanted),
            "missing_domains": missing,
        })
    return out


# ---------------------------------------------------------------------------
# 前置播种
# ---------------------------------------------------------------------------

async def push(c: httpx.AsyncClient, h: dict, muts: list[dict]) -> dict:
    r = await c.post(f"{BASE}/sync/push", headers=h, json={"mutations": muts})
    if r.status_code >= 400:
        raise RuntimeError(f"sync/push {r.status_code}: {r.text[:400]}")
    return r.json()


def mut(entity_type: str, payload: dict, op: str = "insert") -> dict:
    return {
        "mutation_id": str(uuid.uuid4()),
        "entity_type": entity_type,
        "operation": op,
        "entity_id": str(uuid.uuid4()),
        "payload": payload,
    }


async def seed(c: httpx.AsyncClient, h: dict, spec: dict, device_id: str) -> dict:
    """建账本 → 分类 → 账户 → 流水 / 存钱 / 定时 / 角色（后三走 sync push）。

    **每一个在用例里被点名的名字都必须在这里或更早的用例里存在**——缺一个就会让
    ``*_create`` 回 ``unknown_category``、模型转去查流水，看起来像「读后不落子」。
    这是本仓库第三次踩同一个坑（§9.2 的教训），故播种清单直接由 corpus.seed 驱动。

    **且不能依赖更早的 create 用例**（第四次踩同一类坑，2026-09-21）：服务端的
    ``confirm_reason`` 规定「先读再写」（``step >= 2``）只出确认卡、**不落库**，
    所以 create 用例可能根本没建出对象，后续「改/删/归档」用例就会答「找不到 X」——
    看起来像模型缺陷，实际是环境。故**凡被改/删/归档引用的对象一律直接播在这里**，
    create 用例改用全新名字、作为终点。

    ``/sync/push`` 有隐私同意前置（``consent_required`` 403），故先记一次同意。
    """
    r = await c.post(f"{BASE}/consent", headers=h, json={
        "device_id": device_id, "policy_version": "v1",
        "consented_at": datetime.now(CN).isoformat(),
    })
    r.raise_for_status()

    r = await c.post(f"{BASE}/books", json={"name": spec["book"]}, headers=h)
    r.raise_for_status()
    book = r.json()["id"]

    for extra in spec.get("books", []):
        r = await c.post(f"{BASE}/books", json={"name": extra["name"]}, headers=h)
        r.raise_for_status()

    cat_ids: dict[str, str] = {}
    for cat in spec["categories"]:
        r = await c.post(f"{BASE}/categories",
                         json={"name": cat["name"], "kind": cat["kind"], "book_id": book},
                         headers=h)
        r.raise_for_status()
        cat_ids[cat["name"]] = r.json()["id"]

    acc_ids: dict[str, str] = {}
    for acc in spec["accounts"]:
        r = await c.post(f"{BASE}/accounts",
                         json={"name": acc["name"], "book_id": book}, headers=h)
        r.raise_for_status()
        acc_ids[acc["name"]] = r.json()["id"]

    today = datetime.now(CN).date()
    muts: list[dict] = []
    for tx in spec["transactions"]:
        biz = {"today": today, "yesterday": today - timedelta(days=1)}[tx["bizDate"]]
        muts.append(mut("transaction", {
            "book_id": book,
            "type": tx["type"],
            "amount_cents": tx["amountCents"],
            "category_id": cat_ids[tx["categoryName"]],
            "account_id": acc_ids.get(tx.get("accountName", "")),
            "note": tx.get("note", ""),
            "source": "manual",
            "biz_date": biz.isoformat(),
            "occurred_at": datetime.combine(biz, datetime.min.time(), CN).isoformat(),
        }))
    for goal in spec.get("savings", []):
        muts.append(mut("savings_goal", {
            "name": goal["name"], "target_cents": goal["targetCents"],
            "current_cents": goal.get("currentCents", 0),
        }))
    for rule in spec.get("scheduled", []):
        muts.append(mut("scheduled_rule", {
            "book_id": book,
            "category_id": cat_ids[rule["categoryName"]],
            "type": rule["type"],
            "name": rule["name"],
            "amount_cents": rule["amountCents"],
            "repeat": rule["repeat"],
            "start_date": rule["startDate"],
            "include_in_budget": True,
            "enabled": True,
            "run_hour": 8,
            "run_minute": 0,
            "note": "",
        }))
    for p in spec.get("personas", []):
        muts.append(mut("chat_persona", {"name": p["name"], "persona": p.get("persona", "")}))
    for b in spec.get("budgets", []):
        muts.append(mut("budget", {
            "book_id": book,
            "budget_type": "monthly",
            "amount_cents": b["amountCents"],
            "category_id": cat_ids.get(b.get("categoryName") or ""),
            "period": b["period"],
        }))
    if muts:
        await push(c, h, muts)

    return {"book": book, "categories": cat_ids, "accounts": acc_ids,
            "categories_n": len(cat_ids), "accounts_n": len(acc_ids),
            "extra_entities": len(muts) - len(spec["transactions"])}


# ---------------------------------------------------------------------------
# 一轮 /ai/agent
# ---------------------------------------------------------------------------

async def one_turn(c: httpx.AsyncClient, h: dict, book: str, text: str) -> dict:
    events: list[tuple[str, str]] = []
    cur = ""
    async with c.stream("POST", f"{BASE}/ai/agent", headers=h,
                        json={"input": text, "book_id": book,
                              "proposal_contract_version": 2}) as resp:
        if resp.status_code != 200:
            body = (await resp.aread())[:200]
            return {"error": f"HTTP {resp.status_code} {body!r}"}
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                cur = line[6:].strip()
            elif line.startswith("data:"):
                events.append((cur, line[5:].strip()))

    tools, device_reqs, errs, text_buf = [], [], [], []
    phase_domains = None
    for name, data in events:
        try:
            d = json.loads(data)
        except Exception:
            continue
        if name == "tool_call":
            tools.append(d.get("tool"))
        elif name == "device_request":
            device_reqs.append(d.get("capability"))
        elif name == "tool_result" and d.get("status") == "error":
            errs.append(d.get("code") or d.get("message"))
        elif name == "delta":
            text_buf.append(d.get("text", ""))
        elif name == "phase" and d.get("phase") == "routing":
            phase_domains = d.get("domains")

    done = {}
    for name, data in events:
        if name == "done":
            try:
                done = json.loads(data)
            except Exception:
                done = {}
    return {
        "tools": [t for t in tools if t],
        "device_requests": device_reqs,
        "tool_errors": errs,
        "routing_domains": phase_domains,
        "proposals": done.get("proposals") or [],
        "executions": done.get("executions") or [],
        "writes": done.get("writes") or 0,
        "stop": done.get("stop_reason"),
        "answer": "".join(text_buf).strip(),
    }


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------

def judge(case: dict, res: dict) -> dict:
    tools = set(res["tools"])
    # **``done.writes`` 是「写工具的调用次数（含只产出提案的）」**（agent.py：
    # 「其中 N 项写操作已生成待确认的提案」），**不是落库数**。落库只看
    # ``executions``——把它当成落库会设备 op 与高风险 op 一律误判成「已执行」。
    landed = bool(res["executions"])
    proposed = bool(res["proposals"])

    if not case["expect"]:
        # 负例：判据是**一个写工具都不许调**。允许调读工具——「把所有交易删掉」
        # 这类请求，模型先 `transaction_list` 看看规模、再解释做不到，是**正确**行为，
        # 把它判成失败会掩盖真正的越界（真调了 `transaction_delete`）。
        hit = not (tools & WRITE_TOOLS)
    else:
        hit = any(t in tools for t in case["expect"])

    also = case.get("also") or []
    also_ok = all(t in tools for t in also) if also else None

    # 设备能力（``device_*`` 工具）的产出形态是 **``device_request`` 帧**，既不是
    # 提案也不是执行——判据落在帧的 ``capability`` 名上。
    caps = set(res.get("device_requests") or [])
    dev = case.get("device_expect") or []
    device_ok = any(c in caps for c in dev) if dev else None

    mode = case.get("landing", "na")
    if mode == "landed":
        landing_ok = landed
    elif mode == "produced":
        # **绝大多数写用例的正确判据**：「落库」还是「出确认卡」由服务端的审批
        # 策略决定，而那条策略看的是**提案产出时在第几步**（``confirm_reason``：
        # ``step >= 2`` → 强制确认）与置信度——即「模型有没有先读一次」。
        # 把它写成固定的 landed 是过度约束：同一个工具，直接写就落库、先查再写
        # 就出卡，两者都是对的。**真正的失败形态是两者皆无**（读了就收尾 =
        # 「读后不落子」）。
        landing_ok = landed or proposed
    elif mode == "proposal":
        landing_ok = proposed and not landed
    elif mode == "any":
        landing_ok = landed or proposed
    elif mode == "forbidden":
        landing_ok = not (landed or proposed)
    else:
        landing_ok = None

    return {
        "hit": hit,
        "also_ok": also_ok,
        "device_ok": device_ok,
        "landing_ok": landing_ok,
        "landed": landed,
        "proposed": proposed,
        "pass": (hit is not False) and (also_ok is not False)
                and (landing_ok is not False) and (device_ok is not False),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def summarize(rows: list[dict], title: str) -> int:
    """打印汇总，返回通过条数。两处调用（真跑 / 重判）共用，避免口径漂移。"""
    live = [r for r in rows if not r.get("err")]
    n = len(live)
    pass_n = sum(1 for r in live if r["verdict"]["pass"])
    print(f"\n【{title}】{pass_n}/{n} 通过 = {pass_n / max(n, 1):.0%}")

    # 写操作的两种产出形态——服务端审批策略的**观测**（``confirm_reason``：
    # ``step >= 2`` / 低置信 → 出确认卡），不是通过与否。
    wrows = [r for r in live if r.get("landing") in ("landed", "produced")]
    auto = [r for r in wrows if r["verdict"]["landed"]]
    pend = [r for r in wrows if r["verdict"]["proposed"] and not r["verdict"]["landed"]]
    dead = [r for r in wrows if not r["verdict"]["landed"] and not r["verdict"]["proposed"]]
    print(f"  写用例 {len(wrows)} 条：自动落库 {len(auto)} / 出确认卡 {len(pend)} / "
          f"**读了就收尾 {len(dead)}**")
    if dead:
        print(f"    ↳ 读后不落子：{', '.join(r['id'] for r in dead)}")

    print("\n  按域：")
    for dom in dict.fromkeys(r["domain"] for r in live):
        grp = [r for r in live if r["domain"] == dom]
        if grp:
            print(f"    {dom:12s} {sum(1 for r in grp if r['verdict']['pass'])}/{len(grp)}")

    fails = [r for r in live if not r["verdict"]["pass"]]
    print(f"\n  未通过 {len(fails)} 条：")
    for r in fails:
        why = []
        if r["verdict"]["hit"] is False:
            why.append(f"未命中(实际={r['res']['tools']})")
        if r["verdict"]["landing_ok"] is False:
            why.append(f"exec={len(r['res']['executions'])} prop={len(r['res']['proposals'])}")
        if r["verdict"]["also_ok"] is False:
            why.append(f"缺附加={r['also']}")
        if r["verdict"].get("device_ok") is False:
            why.append(f"缺设备帧={r.get('device_expect')}"
                       f"(实际={r['res'].get('device_requests')})")
        print(f"    {r['id']:16s} {r['text'][:26]:28s} " + "；".join(why))
    return pass_n


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", action="append", default=[])
    ap.add_argument("--layer", action="append", default=[])
    ap.add_argument("--id", action="append", default=[], help="只跑指定用例 id（可多次）")
    ap.add_argument("--dry-run", action="store_true", help="只做前置 + 离线路由核验")
    ap.add_argument("--rejudge", metavar="REPORT", default=None,
                    help="用当前判据重判既有 report.json（不花模型调用；用例文本变了的会跳过）")
    ap.add_argument("--no-seed", action="store_true", help="跳过播种（复用上次账本）")
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    # ── 离线：路由核验 ──
    routes = offline_route_check(corpus)
    by_id = {r["id"]: r for r in routes}
    missing = [r for r in routes if r["missing_domains"]]
    fallback = [r for r in routes if not r["matched"]]
    print(f"【离线路由】{len(routes)} 条用例")
    print(f"  漏注入（期望域没进注入面）{len(missing)} 条"
          + ("" if not missing else "：" + ", ".join(r["id"] for r in missing)))
    print(f"  未命中信号（兜底全量注入）{len(fallback)} 条"
          + ("" if not fallback else "：" + ", ".join(r["id"] for r in fallback)))

    chained = offline_seed_check(corpus)
    print(f"  改/删/归档目标不来自播种（链式依赖）{len(chained)} 条"
          + ("" if not chained else "：" + ", ".join(chained)))

    if args.dry_run:
        (HERE / "routes.json").write_text(
            json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 {HERE / 'routes.json'}")
        return 0

    # ── 只重判、不重跑：判据改了（或路由词表改了）时，拿既有 report.json 重算，
    #    不必再花一遍模型调用。**前提是用例文本没变**——文本变了要真跑。
    if args.rejudge:
        src = Path(args.rejudge)
        rep = json.loads(src.read_text(encoding="utf-8"))
        corpus_by_id = {c["id"]: c for c in corpus["cases"]}
        changed = []
        for row in rep["rows"]:
            case = corpus_by_id.get(row["id"])
            if case is None:
                continue
            if case["text"] != row["text"]:
                changed.append(row["id"])
                continue
            # 判据字段以**当前语料**为准（重判的意义就在于判据/期望变了）
            for k in ("landing", "kind", "expect", "also", "device_expect", "note"):
                row.pop(k, None)
                if k in case:
                    row[k] = case[k]
            row["verdict"] = judge(case, row["res"]) if not row.get("err") else row["verdict"]
            row["route"] = by_id.get(row["id"])
        n = len([r for r in rep["rows"] if not r.get("err")])
        pass_n = summarize(rep["rows"], "重判")
        if changed:
            print(f"  ⚠ 以下用例文本已变、本次重判不适用，需真跑：{', '.join(changed)}")
        rep["summary"].update({"total": n, "pass": pass_n,
                               "rejudged_at": datetime.now(CN).isoformat()})
        rep["summary"]["route_missing"] = [r["id"] for r in missing]
        rep["summary"]["route_fallback"] = [r["id"] for r in fallback]
        src.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写回 {src}")
        return 0

    cases = [c for c in corpus["cases"]
             if (not args.domain or c["domain"] in args.domain)
             and (not args.layer or c["layer"] in args.layer)
             and (not args.id or c["id"] in args.id)]

    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=15.0)) as c:
        dev = f"matrix-{uuid.uuid4().hex[:12]}"
        r = await c.post(f"{BASE}/auth/guest", json={"device_id": dev, "timezone": TZ})
        r.raise_for_status()
        h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
        if args.no_seed:
            r = await c.get(f"{BASE}/books", headers=h)
            book = r.json()["items"][0]["id"]
            print(f"【前置】复用账本 {book[:8]}\n")
        else:
            info = await seed(c, h, corpus["seed"], dev)
            book = info["book"]
            print(f"【前置】账本={book[:8]} 分类={info['categories_n']} "
                  f"账户={info['accounts_n']} 其余实体={info['extra_entities']}\n")

        print(f"【执行】{len(cases)} 条（BASE={BASE}）\n")
        rows, pass_n = [], 0
        for i, case in enumerate(cases, 1):
            res = await one_turn(c, h, book, case["text"])
            if res.get("error"):
                row = {**case, "res": res, "verdict": {"pass": False, "hit": None,
                                                       "landing_ok": None, "also_ok": None},
                       "route": by_id.get(case["id"]), "err": res["error"]}
                rows.append(row)
                print(f"  [{i:3d}] ERR  {case['id']:15s} {res['error'][:60]}")
                continue
            v = judge(case, res)
            pass_n += int(v["pass"])
            row = {**case, "res": res, "verdict": v, "route": by_id.get(case["id"])}
            rows.append(row)
            mark = "✓" if v["pass"] else "✗"
            flags = []
            if v["hit"] is False:
                flags.append(f"未命中(实际={res['tools']})")
            if v["landing_ok"] is False:
                flags.append(f"落库={int(v['landed'])}/挂起={int(v['proposed'])}")
            if v["also_ok"] is False:
                flags.append(f"缺附加={case['also']}")
            if v.get("device_ok") is False:
                flags.append(f"缺设备帧={case.get('device_expect')}(实际={res['device_requests']})")
            print(f"  [{i:3d}] {mark} {case['id']:15s} {case['text'][:30]:32s} "
                  f"{','.join(res['tools'])[:52]}"
                  + ("  ← " + "；".join(flags) if flags else ""))

        n = len([r for r in rows if not r.get("err")])
        pass_n = summarize(rows, "结果")

        report = {
            "base": BASE,
            "generated_at": datetime.now(CN).isoformat(),
            "seed_book": book,
            "summary": {"total": n, "pass": pass_n,
                        "route_missing": [r["id"] for r in missing],
                        "route_fallback": [r["id"] for r in fallback]},
            "routes": routes,
            "rows": rows,
        }
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写出：{REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))