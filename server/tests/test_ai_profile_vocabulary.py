"""T19-3（REQ-74 决策 #4）：画像词汇表**跨三处同源**的守护用例。

同一份 7 键词汇表散在三个地方，任一处漂移的后果不同、而且**都不报错**：

- ``prompts._PROFILE_LABELS`` —— 渲染系统提示的【用户画像】段（AI 读到的标签）；
- ``tools._PROFILE_KEYS_HINT`` —— **写给模型看的那一份**，模型照它写键名；
- ``UserSettingsPage.FIELD_ORDER`` —— 表单页按它取值，只认这 7 个键。

漂移的形态就是本卡要修的那个 bug 本身：模型写 ``{"说话偏好": "温柔一点"}``，
表单页按 ``nickname`` / ``communicationStyle`` 取值 → **显示为空**，而数据其实
已经存进库里了。所以这三条断言不是形式主义，是那条症状的回归网。

**为什么从 Python 读 ``.tsx``**：跨端词汇表只有一条断言能同时看见两边。范式同
``test_validator_parity``（读仓库根的共享契约 fixture）——同样用
``Path(__file__).resolve().parents[2]`` 定位仓库根。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.modules.ai import tools
from src.modules.ai.prompts import _PROFILE_LABELS  # noqa: PLC2701 - 守护用例必须读那一份

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS_PAGE = (
    _REPO_ROOT / "app" / "src" / "features" / "settings" / "UserSettingsPage.tsx"
)

#: ``const FIELD_ORDER: (keyof ProfileForm)[] = [ 'a', 'b', … ]``——数组字面量的
#: 内容。**只认这一个形状**：找不到就报错，绝不静默返回空集（否则表单页一改
#: 写法，这条守护用例就会以「空集 == 空集」的形式假绿）。
_FIELD_ORDER_RE = re.compile(r"const FIELD_ORDER[^=]*=\s*\[(.*?)\]", re.S)


def _form_field_order() -> list[str]:
    """从表单页源码里取出 ``FIELD_ORDER`` 的键序。"""
    source = _SETTINGS_PAGE.read_text(encoding="utf-8")
    match = _FIELD_ORDER_RE.search(source)
    assert match is not None, (
        f"没在 {_SETTINGS_PAGE} 里找到 FIELD_ORDER 的数组字面量——表单页改了形状，"
        "这条用例要跟着改（**不要**让它静默通过）"
    )
    keys = re.findall(r"'([^']+)'", match.group(1))
    assert keys, f"FIELD_ORDER 解析出了空键集，源码形状可能变了：{match.group(1)!r}"
    return keys


class TestProfileVocabularyParity:
    def test_prompt_labels_match_the_settings_form(self):
        """服务端标签表的键集 == 表单页 ``FIELD_ORDER`` 的键集（决策 #4 的那一条）。

        两处是**同一份词汇表的两端**：服务端用它渲染【用户画像】段与过滤 AI
        写进来的键，表单页用它取值渲染。今天它们只是**巧合地**一致，这条用例
        把它变成一条约束。
        """
        assert set(_PROFILE_LABELS) == set(_form_field_order())

    def test_the_meta_set_tool_shows_those_keys_to_the_model(self):
        """光有常量不算数——它必须真的出现在**发给模型的描述**里。

        这是本卡的修复本体：模型之前只知道「用户画像 JSON 字符串」这句，于是按
        自然语言编键名。只钉住 ``_PROFILE_LABELS == FIELD_ORDER`` 而漏掉这一条，
        等于守护了一个模型根本看不到的东西。
        """
        description = tools.TOOLS_BY_NAME["meta_set"].parameters["properties"][
            "profile"
        ]["description"]

        for key, label in _PROFILE_LABELS.items():
            assert f"{key}（{label}）" in description, (
                f"meta_set 的 profile 描述里没有 {key}（{label}）——模型又会编键名"
            )

    def test_the_hint_lists_exactly_the_profile_keys(self):
        """提示串与标签表逐项一致、且不多不少（``_PROFILE_KEYS_HINT`` 由标签表
        生成，所以这条是在钉「生成关系还在」——有人把它改成手写常量时会红）。"""
        assert tools._PROFILE_KEYS_HINT == " / ".join(  # noqa: SLF001
            f"{key}（{label}）" for key, label in _PROFILE_LABELS.items()
        )
