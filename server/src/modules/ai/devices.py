"""T-M7-05e: 设备能力通道（服务端）——协议、能力目录与挂起表。

架构依据 §4.3（设备能力通道）、§8.1（``device_request`` 事件）与 §8.4（单 worker
的部署前提），决策登记 **ADR-010**。

两类协议
--------
::

    单向（one_way）   loop 产出设备指令 → SSE 下发 → 客户端执行 → **不回传**
    需结果（result）  loop **暂停** → SSE 下发 → 客户端执行 → HTTP 上行回传 → 恢复

判据是「结果有没有下游消费者」：分享文本 / 刷新小组件 / 触感反馈不影响后续推理，
为它们引入一次往返与一份挂起态纯属浪费；而权限申请 / 图片选择的结果是任务继续的
必要输入。

本模块承载通道的**协议面**，不含任何执行语义：

- :class:`DeviceTool` 与 :data:`DEVICE_TOOLS` —— 能力目录（**不是** ``tools.py`` 的
  工具注册表：设备工具没有 DB handler、执行体在客户端、执行语义分两类）；
- :class:`DeviceRequest` —— ``device_request`` 帧的载荷（完整信封）；
- :class:`DeviceChannel` —— 需结果类的**进程内挂起表**（ADR-010 决策 2）。

执行语义（超时 / 拒绝 / 失败如何回喂模型、挂起期间的取消）在 ``agent.py`` 的
``AgentLoop._device_call``——本模块只负责「把请求记下来、被回传唤醒、返回一个
结局」，因此不依赖 ``ToolOutcome`` 与 loop。

能力名（``capability``）是**协议层**的名字，用点号；**到桥命令的映射归客户端**
（05f 的分派表），协议不对映射的基数作要求（当前目录里每个能力恰好落一条桥命令）。
工具名（``name``）走 provider，沿用下划线（点号会让 provider 直接 400，02c 实测）。
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from jsonschema import Draft7Validator

from .tools import ToolRisk
from .validator import _FORMAT_CHECKER

# ---------------------------------------------------------------------------
# 类别与结局
# ---------------------------------------------------------------------------

#: 单向指令：下发即继续，**不等回传**（结果不影响后续推理）。
ONE_WAY = "one_way"
#: 需结果：loop 挂起，等 HTTP 上行回传后继续。
NEEDS_RESULT = "result"

DeviceKind = Literal["one_way", "result"]

#: 一次需结果请求的结局。前四个来自客户端回传，``timeout`` 由服务端产生
#: （挂起期满仍未回传）——它是**收敛路径**，不是错误：回合照常往下走，
#: 只是模型拿到的是一条「没等到结果」的失败结果（ADR-010 决策 4）。
STATUS_OK = "ok"
STATUS_DENIED = "denied"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"

#: 客户端**允许**回传的取值（``timeout`` 不在其中：它只能由服务端产生）。
DELIVERABLE_STATUSES: frozenset[str] = frozenset({
    STATUS_OK, STATUS_DENIED, STATUS_UNAVAILABLE, STATUS_FAILED,
})

#: 协议层的域标识。设备工具**不在**域路由的域清单里——``routing`` 只覆盖
#: ``tools.DOMAINS``（05g 落地时未纳入设备域），设备能力在 ``AI_DEVICE_CHANNEL_ENABLED``
#: 开启时由 ``agent._device_tools`` **全量**注入，与选域正交。这个值只用于测试与
#: 协议层的域标注。
DEVICE_DOMAIN = "device"


@dataclass(frozen=True, slots=True)
class DeviceOutcome:
    """一次设备请求的结局（回传的或超时的）。

    ``data`` 只在 ``ok`` 时可能有值，且**形状由能力自己的实现决定**——服务端
    不校验它（payload 的所有权在能力侧，与 v2 契约同口径）。它随后作为配对的
    tool 结果回喂模型，因此同样过 ``agent._without_ids`` 剥掉内部 id。
    """

    status: str
    data: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 能力目录
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DeviceTool:
    """一个设备能力（模型可见的工具描述 + 协议层的派发键）。

    ``description`` 是**模型看到的唯一语义来源**，与 ``tools.py`` 的工具同口径：
    人工撰写、不机器直译。``parameters`` 是 Draft-07，参数只收原始值（模型给的
    名字 / 枚举，不收 id）。

    ``risk`` 与 ``tools.Tool.risk`` **同款同判据**（判据原文见那一边的 docstring），
    但**行为含义不同**：设备能力**不参与服务端审批策略**——它们在
    ``AgentLoop._handle_proposal`` 的第一步就一律挂起（服务端没有它们的存储位置，
    见 ``tools.DEVICE_EXECUTED_OPS``），审批发生在设备侧的确认卡上。这里的取值
    因此是**宣告性的**：它让「哪些指令低风险 / 哪些要人工确认」这份清单对设备能力
    也完整，并让新增能力漏标在导入期即红。
    """

    name: str
    capability: str
    kind: DeviceKind
    risk: ToolRisk
    description: str
    parameters: dict[str, Any]
    #: 步骤卡片上的人类可读文案（``stream.tool_progress`` 的兜底来源）。
    message: str


def _no_params() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


#: 服务端的设备能力目录。**只放已在客户端就位的桥命令族**（架构 §3.2 的
#: 「设备」行）：权限 / 图片 / 分享 / 触感 / 小组件。提醒（``reminder.*``）
#: 与图标 / 主题背景不在其中——前者是写工具（写的是用户的提醒配置，要过审批门，
#: 进 ``tools.py`` 的注册表），后两者是记账域与用户设定的附属能力，各有各的归属。
DEVICE_TOOLS: tuple[DeviceTool, ...] = (
    DeviceTool(
        name="device_haptic",
        capability="haptic.feedback",
        kind=ONE_WAY,
        risk="low",
        description=(
            "在用户设备上给一次触感反馈（震动）。用于「已记好」「办完了」这类确认"
            "时刻，或提示出错。不需要结果，调用后立即返回。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["success", "error"],
                    "description": "success=完成提示，error=出错提示。省略时按 success。",
                },
            },
            "additionalProperties": False,
        },
        message="正在给一次触感反馈…",
    ),
    DeviceTool(
        name="device_widget_refresh",
        capability="widget.refresh",
        kind=ONE_WAY,
        risk="low",
        description=(
            "刷新用户主屏幕上的小组件，让刚写入的数据立刻显示出来。记账等写操作"
            "完成后调用；不需要结果。"
        ),
        parameters=_no_params(),
        message="正在刷新小组件…",
    ),
    DeviceTool(
        name="device_share_text",
        capability="share.text",
        kind=ONE_WAY,
        risk="low",
        description=(
            "调起用户设备的系统分享面板，分享一段文本（例如账单摘要）。文本要"
            "短、是给外人看的，不要含账户余额以外的隐私细节。不需要结果。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": "要分享的文本。",
                },
                "subject": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "分享标题，可省略。",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        message="正在调起分享…",
    ),
    DeviceTool(
        name="device_request_permission",
        capability="permissions.request",
        kind=NEEDS_RESULT,
        risk="low",
        description=(
            "向用户申请一项系统权限（弹出系统权限对话框），返回 granted / denied / "
            "permanently_denied。只在确有必要时调用：拍照前申请 CAMERA，读取相册前"
            "申请 READ_MEDIA_IMAGES，设置提醒前申请 POST_NOTIFICATIONS。被拒绝时"
            "不要反复申请，先向用户说明。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "permission": {
                    "type": "string",
                    "enum": ["CAMERA", "READ_MEDIA_IMAGES", "POST_NOTIFICATIONS"],
                    "description": "要申请的权限。",
                },
            },
            "required": ["permission"],
            "additionalProperties": False,
        },
        message="正在申请权限…",
    ),
    DeviceTool(
        name="device_pick_image",
        capability="image.pick",
        kind=NEEDS_RESULT,
        risk="low",
        description=(
            "让用户从相册选一张图或直接拍一张，返回图片附件（随后可用 "
            "image.parse 之类的服务端能力解析）。用户取消时返回 denied。"
            "拍照前可能需要先申请 CAMERA 权限。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["camera", "gallery"],
                    "description": "camera=拍照，gallery=从相册选。",
                },
            },
            "required": ["source"],
            "additionalProperties": False,
        },
        message="正在等待选择图片…",
    ),
)

DEVICE_TOOLS_BY_NAME: dict[str, DeviceTool] = {
    tool.name: tool for tool in DEVICE_TOOLS
}

#: 参数校验器（与 ``tools.py`` 同款：``format: date`` 之类的断言必须真的生效）。
_DEVICE_VALIDATORS: dict[str, Draft7Validator] = {
    tool.name: Draft7Validator(tool.parameters, format_checker=_FORMAT_CHECKER)
    for tool in DEVICE_TOOLS
}


def device_tools() -> list[DeviceTool]:
    """全部设备能力（loop 注入用）。"""
    return list(DEVICE_TOOLS)


def get_device_tool(name: str) -> DeviceTool | None:
    """按名取设备能力；未知名字返回 ``None``（降级契约的入口）。"""
    return DEVICE_TOOLS_BY_NAME.get(name)


def validate_arguments(
    tool: DeviceTool, params: Mapping[str, Any],
) -> list[str]:
    """校验一次设备调用的参数，返回**错误字段路径**（空 = 通过）。

    路径里不含取值（与 ``tools.py`` 的脱敏口径一致：实例值可能是用户原话）。
    参数不合 schema 由 loop 转成一条配对的 ``invalid_arguments`` 结果，让模型
    自己改——不掀掉整轮对话（架构 §11.1）。
    """
    errors = sorted(
        _DEVICE_VALIDATORS[tool.name].iter_errors(dict(params)),
        key=lambda e: list(e.absolute_path),
    )
    paths: list[str] = []
    for err in errors:
        path = "/".join(str(p) for p in err.absolute_path) or "(root)"
        if err.validator == "required" and isinstance(err.instance, Mapping):
            path = "/".join(
                name for name in err.validator_value if name not in err.instance
            ) or path
        if path not in paths:
            paths.append(path)
    return paths[:4]


# ---------------------------------------------------------------------------
# 请求信封
# ---------------------------------------------------------------------------

#: ``request_id`` 的前缀。它不是 UUID——客户端只把它当作**回传凭据**原样带回
#: （不解析、不入库），前缀的作用是让它在一堆 id 里一眼可辨、也便于日志检索。
REQUEST_ID_PREFIX = "dev-"


def new_request_id() -> str:
    return f"{REQUEST_ID_PREFIX}{uuid.uuid4()}"


@dataclass(frozen=True, slots=True)
class DeviceRequest:
    """一次设备指令的**完整信封**（ADR-010 决策 1）。

    ``payload`` 是模型给的原始参数（已过 schema 校验）——服务端不加工它：
    值域与语义由能力自己的实现（客户端执行器，05f）负责，与 v2 契约
    「payload 的所有权在 op 侧」同一取舍。
    """

    request_id: str
    tool: DeviceTool
    step: int
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> DeviceKind:
        return self.tool.kind

    def to_payload(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        """SSE ``device_request`` 帧的载荷（``step`` 由调用方补——它与其它事件的
        ``step`` 同源，由帧那一层统一带上）。"""
        data: dict[str, Any] = {
            "request_id": self.request_id,
            "kind": self.tool.kind,
            "capability": self.tool.capability,
            "payload": dict(self.payload),
        }
        if timeout_seconds is not None:
            data["timeout_seconds"] = timeout_seconds
        return data


def build_request(
    tool: DeviceTool, params: Mapping[str, Any], *, step: int,
) -> DeviceRequest:
    """组装一次设备请求（含新 ``request_id``）。"""
    return DeviceRequest(
        request_id=new_request_id(), tool=tool, step=step, payload=dict(params),
    )


# ---------------------------------------------------------------------------
# 挂起表（需结果类）
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _Pending:
    """一条挂起中的设备请求。"""

    future: asyncio.Future[DeviceOutcome]
    #: 发起这一轮的**用户**。回传按它校验归属（ADR-010 决策 3）——别人的
    #: request_id 与不存在的 request_id 得到同一个答案（404），不泄漏存在性。
    user_id: uuid.UUID


class DeviceChannel:
    """需结果类设备请求的**进程内挂起表**（ADR-010 决策 2）。

    **为什么是内存**：挂起态的生命周期就是一次 Turn（秒级到分钟级），而单 worker
    是本项目的既定部署前提（架构 §8.4，``Dockerfile`` 无 ``--workers``）。为它建表
    = 迁移 + 状态机 + 跨进程恢复语义，代价远超收益。**代价已建项 R-PROD-32**：
    多 worker 时回传可能落到另一个进程（等满超时窗才收敛，不会错写数据）；
    进程重启会丢掉挂起中的请求（客户端的上行拿到 404，按「这次设备操作作废」处理）。

    条目**随 Turn 生死**：``wait`` 的 ``finally`` 一律注销，因此超时、取消、
    正常回传三条路径都不会留下悬挂请求（不变式 3）。
    """

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = timeout_seconds
        self._pending: dict[str, _Pending] = {}

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    @property
    def pending_count(self) -> int:
        """挂起中的请求数（用例断言「没有悬挂」用）。"""
        return len(self._pending)

    def register(self, request: DeviceRequest, *, user_id: uuid.UUID) -> None:
        """登记一条挂起请求。

        **必须在帧发出之前完成**（``agent.AgentLoop._device_call`` 的调用顺序）：
        回传只能由「已经收到帧的客户端」发起，先登记就没有「回传比登记先到」的
        竞态窗口。
        """
        self._pending[request.request_id] = _Pending(
            future=asyncio.get_running_loop().create_future(), user_id=user_id,
        )

    def deliver(
        self, request_id: str, *, user_id: uuid.UUID, outcome: DeviceOutcome,
    ) -> bool:
        """回传一条结果，唤醒挂起的那一轮。返回是否被接受。

        **四种「回不去」的情形一律返回 False**（端点据此回 404，三者不可分辨）：
        没有这条请求、已经回过（重复回传）、已经超时注销、请求属于别人。
        重试因此是安全的——客户端在弱网下重发一次不会被当成错误。

        ``timeout`` 结局**不被接受**：它只能由服务端产生（挂起期满）。
        schemas 层的 ``Literal`` 是第一道，这里是协议边界上的第二道。
        """
        entry = self._pending.get(request_id)
        if entry is None or entry.user_id != user_id or entry.future.done():
            return False
        if outcome.status not in DELIVERABLE_STATUSES:
            return False
        del self._pending[request_id]
        entry.future.set_result(outcome)
        return True

    async def wait(self, request_id: str) -> DeviceOutcome:
        """等一次回传；**超时返回** ``STATUS_TIMEOUT`` 而不是抛异常。

        超时是收敛路径不是故障：模型拿到一条「没等到结果」的失败结果，照常向
        用户交代（ADR-010 决策 4）。挂起期间被取消（断连）时 ``CancelledError``
        照常向上传播，条目由 ``finally`` 注销——不留悬挂请求。
        """
        entry = self._pending.get(request_id)
        if entry is None:
            # 到不了：调用点紧接着 register。纵深防御，不抛。
            return DeviceOutcome(status=STATUS_TIMEOUT)
        try:
            return await asyncio.wait_for(entry.future, self._timeout)
        except TimeoutError:
            return DeviceOutcome(status=STATUS_TIMEOUT)
        finally:
            self._pending.pop(request_id, None)


#: 进程内的默认挂起表（单 worker 前提）。**loop 与上行端点必须用同一张**——
#: 端点是靠 ``request_id`` 找回挂起那一轮的，两处各建一张等于永远找不到。
_channel: DeviceChannel | None = None


def device_channel() -> DeviceChannel:
    """取进程内单例（timeout 取自 ``ai_device_timeout_seconds``）。

    延迟到首次调用才建：配置在测试里是逐用例设置的（``set_config``），import
    时就固化会把测试与运行的配置搅在一起。
    """
    global _channel
    if _channel is None:
        from src.common.config import get_config

        _channel = DeviceChannel(get_config().ai_device_timeout_seconds)
    return _channel


def to_openai_tools(tools: Sequence[DeviceTool]) -> list[dict[str, Any]]:
    """设备能力的 provider 载荷（与 ``tools.to_openai_tools`` 同形状）。

    形状必须一致——两处都是喂给同一个 provider 的 ``tools`` 字段。设备能力不是
    ``tools.Tool``（没有 handler、没有 proposal_action），因此各生成各的清单，
    一致性由用例钉住（``test_device_openai_payload_matches_tool_payload``）。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]
