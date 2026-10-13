"""T21-2（REQ-83）：自动记账文本解析入口的判据先行探针。

**为什么要有这个脚本**：`ADR-016` 决策 7 与 `ai-architecture.md` 定死了「判据先行」
——先拿**两条通道的真实形态语料**打既有 `POST /ai/interpret`，量出命中率与字段
完整率，**够用就复用、不新增端点**（最小改动）。这条判据必须用**真实模型调用**
取证：MockTransport 不经过真实接口，测不出模型面对「支付页文本 / 通知文案」这种
**非口语输入**时会落到哪个 intent 上（本仓已有先例——02c 的点号命名事故就是
Mock 测不出、真实调用一次就暴露）。

口径
----
- 语料按**来源**分组：微信 / 支付宝 / 云闪付 / 银行 App / 银行短信通知，每组
  含**无障碍通道形态**（按行拼装的页面可见文本）与**通知通道形态**（标题+正文）。
- 语料是**按市面常见文案构造的代表性样本**，不是设备实拍。真机实拍语料（含
  各 App 当期真实文案）属 T21-4 现场走查；本探针量的是**结构能力**，结论对
  文案微调不敏感（见下）。
- 请求形态取**最可能的生产形态**：带 `context.today` + `context.categories`
  （分类注入面）；`--bare` 可去掉 context 做对照。
- 判据三条，逐条可量化：
  - **正例 · 金额命中**：`intent=bookkeeping` 且 `type` 正确且 `amountCents` 与
    语料标注一致（金额错＝记错账，比不记更糟）。
  - **正例 · 字段完整**：`note` 含标注商户名（自动记账的商户名唯一落点）且
    `bizDate` 非空合法。
  - **反例 · 不误记**：退款 / 转账（含信用卡还款）/ 理财基金买入 / 红包收发
    四类「不是消费」的文本，**不得**产出 `intent=bookkeeping` 且金额 > 0 的结果
    （记成消费会污染收支统计；这是 T21-2 改动清单 ③ 的判据）。
  - **边界 · 不猜字段**：缺金额 / 缺商户的文本，`amountCents` 不得凭空捏造
    （宁低置信，不猜字段）。

T21-2 实测结论（2026-10-13，DashScope，语料 21 条，四格对照）
------------------------------------------------------------------
| 入口 / 模型 | 凭证正例(12) | 四类反例(8) |
|---|---|---|
| `/ai/interpret` + qwen3.7-flash（当时的默认） | 金额/方向 12/12 | **0/8 全部误记为账目** |
| `/ai/interpret` + qwen-max | 未跑 | **0/8**（换强模型也救不了） |
| `/ai/parse-notification` + qwen3.7-flash | 10/12 | **0/8** |
| `/ai/parse-notification` + qwen-max | **12/12** | **8/8（类目 8/8 精确）** |

两个失败因素**互相独立**，所以两条修法都要做，缺一不可：

1. **表达位**（模型无关）：口述模板的意图空间是 `bookkeeping / edit / delete /
   query / config / chat`，没有「不是消费」的位置——退款只能落成 `income`、
   信用卡还款只能落成 `expense`。这一格换 qwen-max 依旧是 0/8，证明它**不是**
   模型能力问题。→ `prompts.CAPTURE_PARSE` 引入 `kind` / `reason`。
2. **模型能力**（提示词无关）：同一条提示词，flash 级四类反例 8/8 判成账目
   （且 confidence 0.95–1.0，会走自动入账），max 级 8/8 判对。默认
   `ai_provider_model` 正是 flash 级 → 模板必须自带 `model` 覆盖。

旁证：`/ai/interpret` 下凭证正例的金额与方向本来就 12/12 全对——所以「复用」
的直觉是对的，拦住它的只有反例那一格；而反例恰恰是最不能出错的一格
（记错一笔退款＝凭空多一笔收入）。

成本：`/ai/parse-notification` + qwen-max 平均 **935 tokens / 2.26s** 每次调用
（21 次样本）；调用照常计入 `ai_usage`、guest 日配额照常生效。

用法::

    cd server && python scripts/probe_autobook_text_parse.py                    # 打 /ai/interpret（判据先行的第一轮）
    cd server && python scripts/probe_autobook_text_parse.py --endpoint capture # 打 /ai/parse-notification（新增端点）
    cd server && python scripts/probe_autobook_text_parse.py --bare             # 不带 context（对照）
    cd server && python scripts/probe_autobook_text_parse.py --json out.json

**它不写库、不落任何状态**（只消耗 guest 配额），可随时重跑。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

BASE_DEFAULT = "http://127.0.0.1:8011/api/v1"
TZ = "Asia/Shanghai"
TODAY = "2026-10-13"

#: 分类注入面（分类名白名单，模拟生产形态；口径见 `prompts._render_categories_section`）。
CATEGORIES = [
    "餐饮", "交通", "购物", "娱乐", "日用", "住房", "通讯",
    "医疗", "教育", "服饰", "人情", "其他",
]

# ---------------------------------------------------------------------------
# 语料：两条通道 × 五个来源 + 四类反例 + 边界
# ---------------------------------------------------------------------------
# 字段：id / channel / source / text / 判据参数
#   正例：expect_type + amount_cents + merchant（字段完整判据）
#   反例：negative = 期望的四类 reason（refund / transfer / credit_card_repayment /
#         investment / red_packet）
#   边界：no_amount / no_merchant

CORPUS: list[dict] = [
    # ── 微信（com.tencent.mm）─────────────────────────────────────────
    {
        "id": "wx-notif-pay", "channel": "notification", "source": "wechat",
        "text": "[微信支付] 已付款 ¥32.00\n收款方：星巴克(北京国贸店)\n"
                "支付方式：零钱\n时间：2026-10-13 12:30:15",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
    {
        "id": "wx-a11y-result", "channel": "accessibility", "source": "wechat",
        "text": "支付成功\n¥32.00\n星巴克(北京国贸店)\n零钱\n账单详情\n完成",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
    {
        "id": "wx-notif-redpacket-sent", "channel": "notification", "source": "wechat",
        "text": "[微信支付] 已发出红包 ¥8.80\n红包已被领取",
        "negative": "red_packet",
    },
    {
        "id": "wx-notif-refund", "channel": "notification", "source": "wechat",
        "text": "[微信支付] 退款到账通知\n退款金额 ¥32.00\n已原路退回你的零钱",
        "negative": "refund",
    },
    {
        "id": "wx-notif-transfer", "channel": "notification", "source": "wechat",
        "text": "[微信支付] 已转账 ¥500.00\n收款方：张三",
        "negative": "transfer",
    },
    {
        "id": "wx-notif-redpacket-recv", "channel": "notification", "source": "wechat",
        "text": "[微信] 你收到一个红包\n8.80元，已存入零钱",
        "negative": "red_packet",
    },

    # ── 支付宝（com.eg.android.AlipayGphone）──────────────────────────
    {
        "id": "ali-notif-pay", "channel": "notification", "source": "alipay",
        "text": "支付宝\n支付成功 32.00元\n付款给 星巴克咖啡\n付款方式：余额宝",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
    {
        "id": "ali-a11y-bill", "channel": "accessibility", "source": "alipay",
        "text": "账单详情\n-32.00\n交易成功\n星巴克咖啡\n付款方式 余额宝\n"
                "订单号 2026101322001234567890\n创建时间 2026-10-13 12:30",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
    {
        "id": "ali-notif-refund", "channel": "notification", "source": "alipay",
        "text": "支付宝\n退款成功 32.00元\n已退至余额宝",
        "negative": "refund",
    },
    {
        "id": "ali-notif-creditcard-repay", "channel": "notification", "source": "alipay",
        "text": "支付宝\n信用卡还款成功 2,000.00元\n已还入招商银行信用卡(1234)",
        "negative": "credit_card_repayment",
    },
    {
        "id": "ali-notif-yuebao-in", "channel": "notification", "source": "alipay",
        "text": "支付宝\n余额宝 转入成功 1,000.00元",
        "negative": "investment",
    },

    # ── 云闪付（com.unionpay）────────────────────────────────────────
    {
        "id": "up-notif-pay", "channel": "notification", "source": "unionpay",
        "text": "云闪付\n您尾号1234的银联卡于10月13日12:30在星巴克(北京国贸店)"
                "消费人民币32.00元。",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
    {
        "id": "up-a11y-result", "channel": "accessibility", "source": "unionpay",
        "text": "支付成功\n¥32.00\n星巴克(北京国贸店)\n云闪付",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
    {
        "id": "up-notif-no-merchant", "channel": "notification", "source": "unionpay",
        "text": "云闪付\n消费人民币32.00元",
        "expect_type": "expense", "amount_cents": 3200, "no_merchant": True,
    },

    # ── 银行 App（招商银行）──────────────────────────────────────────
    {
        "id": "cmb-notif-pay", "channel": "notification", "source": "bank_app",
        "text": "招商银行\n您尾号1234的储蓄卡10月13日12:30快捷支付支出人民币"
                "32.00元，余额12,345.67元。",
        "expect_type": "expense", "amount_cents": 3200, "merchant": None,
    },
    {
        "id": "cmb-a11y-detail", "channel": "accessibility", "source": "bank_app",
        "text": "交易明细\n支出 32.00元\n商户 星巴克(北京国贸店)\n"
                "交易时间 2026-10-13 12:30:15\n卡号尾号 1234",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
    {
        "id": "cmb-notif-income", "channel": "notification", "source": "bank_app",
        "text": "招商银行\n您尾号1234的储蓄卡10月13日09:00入账人民币12,000.00元"
                "（代发工资），余额21,345.67元。",
        "expect_type": "income", "amount_cents": 1200000, "merchant": None,
    },
    {
        "id": "cmb-notif-wealth-buy", "channel": "notification", "source": "bank_app",
        "text": "招商银行\n您购买朝朝宝活期理财人民币5,000.00元，份额已确认。",
        "negative": "investment",
    },

    # ── 银行短信通知（由通知监听读取，非 READ_SMS）───────────────────
    {
        "id": "icbc-notif-sms-pay", "channel": "notification", "source": "bank_sms",
        "text": "【中国工商银行】您尾号5678的储蓄卡10月13日12:30消费人民币"
                "32.00元，余额8,765.43元。",
        "expect_type": "expense", "amount_cents": 3200, "merchant": None,
    },
    {
        "id": "icbc-notif-sms-income", "channel": "notification", "source": "bank_sms",
        "text": "【中国工商银行】您尾号5678的储蓄卡10月13日09:00收入人民币"
                "8,000.00元，余额16,765.43元。",
        "expect_type": "income", "amount_cents": 800000, "merchant": None,
    },

    # ── 边界：缺字段（宁低置信，不猜字段）───────────────────────────
    {
        "id": "wx-a11y-no-amount", "channel": "accessibility", "source": "wechat",
        "text": "支付成功\n查看账单详情",
        "no_amount": True,
    },

    # ── 对照组：用户口语（既有能力基线，证明问题出在输入形态而非模型本身）──
    {
        "id": "oral-expense", "channel": "user", "source": "oral",
        "text": "星巴克花了32块",
        "expect_type": "expense", "amount_cents": 3200, "merchant": "星巴克",
    },
]


def _judge(item: dict, output: object, mode: str) -> dict:
    """把一条响应判成 verdict（判据见模块 docstring）。"""
    out = output if isinstance(output, dict) else {}
    return _judge_capture(item, out) if mode == "capture" else _judge_interpret(item, out)


def _judge_interpret(item: dict, out: dict) -> dict:
    """`/ai/interpret`（口述中间表示）的判据。"""
    intent = out.get("intent")
    amount = out.get("amountCents")
    amount = amount if isinstance(amount, int) else None
    note = out.get("note") if isinstance(out.get("note"), str) else ""
    biz_date = out.get("bizDate") if isinstance(out.get("bizDate"), str) else ""
    confidence = out.get("confidence")

    if item.get("negative"):
        # 反例：不得产出「记账 + 正金额」
        mis_expensed = intent == "bookkeeping" and bool(amount)
        return {
            "verdict": "PASS" if not mis_expensed else "FAIL",
            "why": "未误记为消费" if not mis_expensed else f"误记为消费({intent},{amount})",
            "intent": intent, "amount": amount, "note": note,
            "bizDate": biz_date, "confidence": confidence,
        }

    if item.get("no_amount"):
        # 边界：不得凭空捏造金额
        fabricated = bool(amount)
        return {
            "verdict": "PASS" if not fabricated else "FAIL",
            "why": "未捏造金额" if not fabricated else f"捏造金额({amount})",
            "intent": intent, "amount": amount, "note": note,
            "bizDate": biz_date, "confidence": confidence,
        }

    # 正例：金额命中 + 字段完整
    type_ok = out.get("type") == item["expect_type"]
    amount_ok = amount == item["amount_cents"]
    merchant = item.get("merchant")
    merchant_ok = None if (merchant is None or item.get("no_merchant")) else (merchant in note)
    # bizDate 只在**语料确实含日期**时才作判据——没有日期的文本输出 "" 是正确
    # 行为（prompts 规则 2：不得臆造日期），自动记账的时间来源本是捕获时间
    # （`auto_captures.postedAt`），不是模型从文本里抠。
    date_required = ("2026-10-13" in item["text"]) or ("10月13日" in item["text"])
    date_ok = None if not date_required else (bool(biz_date) and len(biz_date) >= 8)

    reasons = []
    if intent != "bookkeeping":
        reasons.append(f"intent={intent}")
    if not type_ok:
        reasons.append(f"type={out.get('type')}")
    if not amount_ok:
        reasons.append(f"amount={amount}≠{item['amount_cents']}")
    if merchant_ok is False:
        reasons.append("note缺商户名")
    if date_ok is False:
        reasons.append("bizDate 空")

    return {
        "verdict": "PASS" if not reasons else "FAIL",
        "why": "；".join(reasons) or "全中",
        "amount_ok": amount_ok, "type_ok": type_ok,
        "merchant_ok": merchant_ok, "date_ok": date_ok,
        "intent": intent, "amount": amount, "note": note,
        "bizDate": biz_date, "confidence": confidence,
    }


def _judge_capture(item: dict, out: dict) -> dict:
    """`/ai/parse-notification`（kind/reason 形状）的判据。

    反例的主判据是**没被记成消费**（kind=not_transaction 或金额为 0）；`reason`
    是否精确落进期望的那一类另记一列——四类互混不污染收支统计，但类目错要看得见。
    """
    kind = out.get("kind") if isinstance(out.get("kind"), str) else ""
    reason = out.get("reason") if isinstance(out.get("reason"), str) else ""
    amount = out.get("amountCents")
    amount = amount if isinstance(amount, int) else None
    note = out.get("note") if isinstance(out.get("note"), str) else ""
    biz_date = out.get("bizDate") if isinstance(out.get("bizDate"), str) else ""
    confidence = out.get("confidence")
    base = {"intent": kind, "reason": reason, "amount": amount, "note": note,
            "bizDate": biz_date, "confidence": confidence}

    if item.get("negative"):
        not_expensed = kind == "not_transaction" or not amount
        reason_ok = reason == item["negative"]
        why = "未误记为消费" if not_expensed else f"误记为消费({kind},{amount})"
        if not_expensed and not reason_ok:
            why += f"（reason={reason or '空'}≠{item['negative']}）"
        return {**base, "verdict": "PASS" if not_expensed else "FAIL",
                "reason_ok": reason_ok, "why": why}

    if item.get("no_amount"):
        fabricated = bool(amount)
        return {**base, "verdict": "PASS" if not fabricated else "FAIL",
                "why": "未捏造金额" if not fabricated else f"捏造金额({amount})"}

    # 正例：kind=transaction + 金额 / type / 商户 / 日期
    transaction = kind == "transaction"
    type_ok = out.get("type") == item["expect_type"]
    amount_ok = amount == item["amount_cents"]
    merchant = item.get("merchant")
    merchant_ok = None if (merchant is None or item.get("no_merchant")) else (merchant in note)
    date_required = ("2026-10-13" in item["text"]) or ("10月13日" in item["text"])
    date_ok = None if not date_required else bool(biz_date)

    reasons = []
    if not transaction:
        reasons.append(f"kind={kind}")
    if not type_ok:
        reasons.append(f"type={out.get('type')}")
    if not amount_ok:
        reasons.append(f"amount={amount}≠{item['amount_cents']}")
    if merchant_ok is False:
        reasons.append("note缺商户名")
    if date_ok is False:
        reasons.append("bizDate 空")

    return {
        **base,
        "verdict": "PASS" if not reasons else "FAIL",
        "why": "；".join(reasons) or "全中",
        "amount_ok": amount_ok, "type_ok": type_ok,
        "merchant_ok": merchant_ok, "date_ok": date_ok,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_DEFAULT)
    ap.add_argument("--endpoint", choices=("interpret", "capture"), default="interpret",
                    help="interpret=/ai/interpret（口述入口）；capture=/ai/parse-notification")
    ap.add_argument("--bare", action="store_true", help="不带 context（对照）")
    ap.add_argument("--json", dest="json_path", default=None)
    ap.add_argument("--grep", default=None,
                    help="只跑 id 含该子串的语料（对照实验用，如 --grep refund）")
    ap.add_argument("--only", choices=("positive", "negative", "edge"), default=None,
                    help="只跑某一类语料（对照实验用）")
    args = ap.parse_args()

    context = None if args.bare else {"today": TODAY, "categories": CATEGORIES}
    url = (f"{args.base}/ai/parse-notification" if args.endpoint == "capture"
           else f"{args.base}/ai/interpret")
    cases = [c for c in CORPUS
             if args.endpoint != "capture" or c.get("channel") != "user"]
    if args.grep:
        cases = [c for c in cases if args.grep in c["id"]]
    if args.only == "positive":
        cases = [c for c in cases if c.get("expect_type")]
    elif args.only == "negative":
        cases = [c for c in cases if c.get("negative")]
    elif args.only == "edge":
        cases = [c for c in cases if c.get("no_amount")]

    # 探针按 6.5s 节流，而 uvicorn 的 keep-alive 超时是 5s：复用空闲连接会拿到
    # ConnectError（连接已被服务端关掉）。禁掉 keep-alive 复用，每次新建连接。
    limits = httpx.Limits(max_keepalive_connections=0, max_connections=4)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0), limits=limits,
    ) as c:
        dev = f"t21-2-{uuid.uuid4().hex[:12]}"
        r = await c.post(f"{args.base}/auth/guest",
                         json={"device_id": dev, "timezone": TZ})
        r.raise_for_status()
        h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}

        rows = []
        for i, item in enumerate(cases, 1):
            if args.endpoint == "capture":
                body = {"text": item["text"],
                        "source_channel": item.get("channel", "notification")}
            else:
                body = {"input": item["text"]}
            if context:
                body["context"] = context
            # 限流：每用户 60s 滑窗 10 次（`service._check_rate_limit`）——探针按
            # 6.5s 节流；撞 429 时等满一个窗口重试一次，避免把限流记成 ERR。
            for attempt in (1, 2):
                await asyncio.sleep(0 if i == 1 and attempt == 1 else 6.5)
                started = time.perf_counter()
                try:
                    resp = await c.post(url, json=body, headers=h)
                except Exception as exc:  # noqa: BLE001
                    v = {"verdict": "ERR", "why": f"{type(exc).__name__}: {exc}"}
                    output, usage = None, {}
                    break
                elapsed = round(time.perf_counter() - started, 2)
                if resp.status_code == 429 and attempt == 1:
                    await asyncio.sleep(62)
                    continue
                if resp.status_code != 200:
                    v = {"verdict": "ERR", "why": f"HTTP {resp.status_code}: {resp.text[:120]}"}
                    output, usage = None, {}
                else:
                    data = resp.json()
                    output = data.get("output")
                    usage = data.get("usage") or {}
                    v = _judge(item, output, args.endpoint)
                break
            rows.append({**item, "output": output, "usage": usage,
                         "elapsed_s": elapsed, **v})
            tag = {"PASS": "✓", "FAIL": "✗", "ERR": "!"}[v["verdict"]]
            print(f"  [{i:2d}] {tag} {item['id']:24s} {v['verdict']:4s} "
                  f"{elapsed:5.2f}s {v['why']}")
            print(f"        out={json.dumps(output, ensure_ascii=False)}")

    # ── 汇总 ────────────────────────────────────────────────────────
    def tally(pred) -> tuple[int, int]:
        sel = [x for x in rows if pred(x)]
        return sum(1 for x in sel if x["verdict"] == "PASS"), len(sel)

    pos = [x for x in rows if x.get("expect_type")]
    neg = [x for x in rows if x.get("negative")]
    edge = [x for x in rows if x.get("no_amount")]
    p_ok, p_n = tally(lambda x: x.get("expect_type"))
    amount_hits = sum(1 for x in pos if x.get("amount_ok"))
    type_hits = sum(1 for x in pos if x.get("type_ok"))
    merchant_sel = [x for x in pos if x.get("merchant_ok") is not None]
    merchant_hits = sum(1 for x in merchant_sel if x.get("merchant_ok"))
    date_sel = [x for x in pos if x.get("date_ok") is not None]
    date_hits = sum(1 for x in date_sel if x.get("date_ok"))
    n_ok, n_n = tally(lambda x: x.get("negative"))
    e_ok, e_n = tally(lambda x: x.get("no_amount"))

    label = "/ai/parse-notification" if args.endpoint == "capture" else "/ai/interpret"
    print(f"\n════════ 判据先行汇总（{label}" + (" · bare" if args.bare else "") + "）════════")
    print(f"正例     {p_ok}/{p_n} 全中")
    print(f"  ├ 金额命中   {amount_hits}/{len(pos)}")
    print(f"  ├ type 命中  {type_hits}/{len(pos)}")
    print(f"  ├ 商户名入 note {merchant_hits}/{len(merchant_sel)}")
    print(f"  └ bizDate 非空（仅含日期语料）{date_hits}/{len(date_sel)}")
    print(f"反例(四类) {n_ok}/{n_n} 未误记")
    if args.endpoint == "capture" and neg:
        reason_hits = sum(1 for x in neg if x.get("reason_ok"))
        print(f"  └ 类目精确  {reason_hits}/{len(neg)}（refund/transfer/credit_card_repayment/"
              f"investment/red_packet 落对类）")
    if edge:
        print(f"边界(缺字段) {e_ok}/{e_n} 未捏造")
    print(f"合计     {p_ok + n_ok + e_ok}/{p_n + n_n + e_n}")

    # 成本与耗时（卡片 ④：解析调用必须计入 ai_usage、成本可观测）
    ok_rows = [x for x in rows if x["verdict"] != "ERR" and x.get("usage")]
    if ok_rows:
        tokens = [x["usage"].get("prompt_tokens", 0) + x["usage"].get("completion_tokens", 0)
                  for x in ok_rows]
        times = [x["elapsed_s"] for x in ok_rows]
        print(f"成本/延迟 {len(ok_rows)} 次调用：平均 {sum(tokens)/len(tokens):.0f} tokens、"
              f"平均 {sum(times)/len(times):.2f}s（模型见响应 task/model）")

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({"base": args.base, "endpoint": args.endpoint,
                        "bare": args.bare, "rows": rows},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n已写入 {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))