"""T-M7-05i: 产品帮助语料与检索的守护用例。

验收映射（``docs/milestones/T-M7-05-P4-implementation-plan.md`` §三 05i）：
- 帮助语料只讲**产品怎么用**、不得虚构未实现的功能 → ``TestCorpusHygiene``
- 检索是确定性的、命中率可量化的 → ``TestRetrieval`` + ``TestSampling``
- 少一条帮助是「能力缺口」而不是「报错」 → ``TestToolContract``

**这里不测模型**（模型会不会去调 ``help_search`` 是选择准确率问题，归
R-PROD-17 的真实抽样口径，不在用例里假装测得到）：本文件只钉住
「语料说什么」「检索给什么」「工具怎么回」三件事。
"""

from __future__ import annotations

import asyncio
import re

import pytest

from src.modules.ai import help as H
from src.modules.ai import tools as T
from src.modules.ai import stream as S

#: 语料里**不许出现**的措辞。它们都是「对未来的承诺」——帮助里出现一句产品
#: 做不到的话，模型会照着它向用户承诺，而那句承诺没有任何代码兜底。
#: （「暂不支持」不在此列：如实地写「当前不支持 X」是**陈述事实**，正是要保留的。）
_FORBIDDEN_PROMISES = ("即将", "敬请期待", "规划中", "后续版本", "TODO", "TODO:")

#: 正文长度上限。**这不是审美约束**：每条正文都会进入模型上下文（最多 3 条
#: 一起），长了会把窗口挤掉真实对话——帮助是补充，不该挤占主上下文。
_MAX_BODY_CHARS = 220

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}")


def _invoke(name: str, arguments: dict | None):
    return asyncio.run(T.invoke_tool(name, None, arguments))


# ── 1. 语料自身的卫生 ────────────────────────────────────────────────────


class TestCorpusHygiene:
    def test_topics_exist(self):
        assert len(H.TOPICS) >= 15, "语料太薄：至少覆盖到 App 的主要功能面"

    def test_ids_and_titles_are_unique(self):
        assert len({t.id for t in H.TOPICS}) == len(H.TOPICS)
        assert len({t.title for t in H.TOPICS}) == len(H.TOPICS)

    def test_every_topic_is_filled_in(self):
        for topic in H.TOPICS:
            assert topic.id and topic.id.islower(), topic.id
            assert len(topic.title) >= 4, topic.id
            assert topic.keywords, topic.id
            assert all(len(k) >= 2 for k in topic.keywords), topic.id
            assert len(topic.body) >= 20, topic.id
            assert len(topic.body) <= _MAX_BODY_CHARS, (
                f"{topic.id} 正文 {len(topic.body)} 字符，超过 {_MAX_BODY_CHARS}"
            )

    def test_every_topic_has_an_entry(self):
        """每条都要写明**用户在 App 里走到哪里**。

        没有入口的帮助等于「功能存在但找不到」——那正是帮助要回答的问题。
        """
        for topic in H.TOPICS:
            assert len(topic.entry) >= 4, f"{topic.id} 没写入口"
            assert topic.entry not in topic.body, topic.id

    def test_no_promises_about_the_future(self):
        for topic in H.TOPICS:
            text = f"{topic.title}{topic.entry}{topic.body}{''.join(topic.keywords)}"
            for phrase in _FORBIDDEN_PROMISES:
                assert phrase not in text, f"{topic.id} 含未来承诺：{phrase}"

    def test_no_ids_or_uuids_leak_into_the_corpus(self):
        """语料是**静态事实**，不该出现任何标识符形态的串。

        读工具的结果不回传 id（铁律 1 的同口径），语料里写一个 UUID 出来，
        模型就有机会把它抄给用户。
        """
        for topic in H.TOPICS:
            text = f"{topic.title}{topic.entry}{topic.body}"
            assert not _UUID_RE.search(text), topic.id

    def test_no_financial_advice(self):
        """理财知识是**本卡显式砍掉的范围**（外部数据检索）。

        语料里出现行情 / 收益率 / 投资建议类措辞，等于把砍掉的那半偷偷放回来。
        """
        forbidden = ("收益率", "行情", "基金净值", "股票代码", "推荐购买", "理财产品")
        for topic in H.TOPICS:
            text = f"{topic.title}{topic.body}"
            for phrase in forbidden:
                assert phrase not in text, f"{topic.id} 含理财内容：{phrase}"


# ── 2. 检索行为 ──────────────────────────────────────────────────────────


class TestRetrieval:
    def test_empty_query_returns_nothing(self):
        assert H.search("") == []
        assert H.search("   ") == []
        assert H.search(None) == []

    def test_unknown_query_returns_nothing(self):
        assert H.search("量子力学第三定律") == []

    def test_limit_is_respected(self):
        hits = H.search("怎么用", limit=1)
        assert len(hits) <= 1

    def test_results_are_deterministic(self):
        """同一查询永远同一结果（顺序也一致）。

        Agent 的可解释性与 R-PROD-17 的抽样评估都建立在这条上：排序带随机性，
        误选就无法归因。
        """
        for query in ("导出", "预算", "怎么记一笔账", "帮助"):
            first = [t.id for t in H.search(query)]
            assert first == [t.id for t in H.search(query)], query

    def test_every_topic_is_findable_by_its_own_keywords(self):
        """**自检索守护**：每条语料至少能被自己的一个关键词找到。

        盲区形态是静默的——语料写了、词表没写，用户问到了却命中不了，
        表现为「帮助里没有这一条」。
        """
        for topic in H.TOPICS:
            found = {t.id for t in H.search(topic.keywords[0], limit=len(H.TOPICS))}
            assert topic.id in found, f"{topic.id} 用它的首关键词「{topic.keywords[0]}」找不到"

    def test_keyword_matching_is_bidirectional(self):
        """用户只说半截（「导出」）也要能命中关键词更长的条目。"""
        assert H.search("导出") == H.search("导出到哪")


# ── 3. 工具契约 ──────────────────────────────────────────────────────────


class TestToolContract:
    def test_help_is_its_own_domain_within_the_cap(self):
        assert T.DOMAIN_HELP in T.DOMAINS
        scoped = T.tools_for_domains([T.DOMAIN_HELP])
        assert len(scoped) == 1
        assert len(scoped) <= T.MAX_TOOLS_PER_DOMAIN

    def test_help_tool_is_read_only(self):
        tool = T.get_tool("help_search")
        assert tool is not None
        assert tool.kind == "read"
        assert tool.proposal_action is None

    def test_help_tool_is_injected_in_both_contract_lanes(self):
        """读工具与契约车道无关（05b 起的口径）：旧客户端也拿得到帮助。

        这条不是顺带的——它意味着帮助检索**不需要**客户端改动、不需要新的
        契约版本、不会因为 APK 没升级而缺席。
        """
        for version in T.PROPOSAL_CONTRACTS:
            names = {t.name for t in T.tools_for_domains(T.DOMAINS, contract_version=version)}
            assert "help_search" in names, version

    def test_help_tool_has_progress_text(self):
        assert S.TOOL_PROGRESS["help_search"].endswith("…")

    def test_missing_or_unknown_arguments_degrade(self):
        bad = _invoke("help_search", {})
        assert bad.kind == "error" and bad.data["code"] == T.ERR_INVALID_ARGUMENTS
        bad = _invoke("help_search", {"query": "导出", "extra": 1})
        assert bad.kind == "error" and bad.data["code"] == T.ERR_INVALID_ARGUMENTS

    def test_matched_result_carries_title_entry_and_content(self):
        out = _invoke("help_search", {"query": _SAMPLING[0][0]})
        assert out.kind == "result"
        assert out.data["matched"] is True
        first = out.data["topics"][0]
        assert set(first) == {"title", "entry", "content"}
        assert first["content"].strip()

    def test_matched_result_carries_no_ids(self):
        """读工具结果不回传 id（铁律 1 的同口径）——语料的 ``id`` 字段不出圈。"""
        out = _invoke("help_search", {"query": _SAMPLING[0][0]})
        dumped = repr(out.data)
        assert not _UUID_RE.search(dumped)
        for topic in H.TOPICS:
            assert f"'{topic.id}'" not in dumped


# ── 4. 抽样：真实问法 → 期望条目 ─────────────────────────────────────────


#: **（用户原话, 期望命中的条目 id）**。这是本卡的「准确率实测」口径：一组
#: 真实用户问法，逐条断言**首条命中**是正确的那一条。它同时是语料的覆盖度
#: 守护——把某条帮助的标题改了却没改检索词，这里会红。
#:
#: 断言的是**首条**而不是「包含」：三条结果一起回给模型，排错序不会让答案
#: 消失，但会让模型先读到不相干的一条——排序是本卡能测的那一半。
_SAMPLING: tuple[tuple[str, str], ...] = (
    ("怎么记一笔账", "record"),
    ("这笔金额记错了怎么改", "edit-tx"),
    ("把刚才那笔删掉", "delete-tx"),
    ("首页上都有什么", "home"),
    ("明细页怎么看", "detail"),
    ("搜一下上个月的打车", "search"),
    ("小票拍照记账", "receipt"),
    ("我有两个账本怎么切换", "books"),
    ("怎么新建一个账户", "accounts"),
    ("怎么加一个分类", "categories"),
    ("怎么设置预算", "budget"),
    ("我想攒钱", "savings"),
    ("每月房租自动记账", "scheduled"),
    ("换个聊天角色", "persona"),
    ("怎么用聊天记账", "chat"),
    ("为什么要确认", "confirm-card"),
    ("怎么看历史聊天记录", "conversations"),
    ("麦克风怎么用", "voice"),
    ("报表在哪看", "reports"),
    ("怎么把数据导出来", "export"),
    ("数据会不会丢", "sync"),
    ("怎么换手机", "account-login"),
    ("怎么注销账号", "delete-account"),
    ("提醒几点响", "reminder"),
    ("桌面组件怎么设置", "widget"),
    ("怎么换成深色模式", "theme"),
    ("用户设定在哪", "profile"),
    ("我要提意见", "feedback"),
    ("隐私政策在哪看", "legal"),
)


class TestSampling:
    @pytest.mark.parametrize(("query", "expected"), _SAMPLING)
    def test_first_hit_is_the_expected_topic(self, query, expected):
        hits = H.search(query)
        assert hits, f"「{query}」什么都没命中"
        assert hits[0].id == expected, f"「{query}」首条是 {hits[0].id}，期望 {expected}"

    def test_sampling_covers_the_feature_surface(self):
        """抽样表本身要够宽：每条语料都该至少被一次真实问法覆盖到。"""
        assert {expected for _, expected in _SAMPLING} == {t.id for t in H.TOPICS}