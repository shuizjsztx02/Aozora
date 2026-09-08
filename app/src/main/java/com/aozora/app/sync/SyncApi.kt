package com.aozora.app.sync

/**
 * 同步 HTTP 传输抽象。
 *
 * 生产实现：[HttpSyncApi]（OkHttp）。
 * 测试替身：[FakeSyncApi]（在 androidTest 中注入，断言请求与构造响应）。
 *
 * 契约遵循 docs/architecture/overview.md §sync-protocol。
 */
interface SyncApi {

    /**
     * POST /sync/push — 推送本地 pending mutations。
     *
     * @return [PushResponse]，包含 accepted/rejected/next_cursor。
     * @throws SyncHttpException 非 2xx 响应（含 HTTP 状态码 + body 摘要）。
     * @throws java.io.IOException 传输层失败。
     */
    suspend fun push(request: PushRequest): PushResponse

    /**
     * POST /sync/pull — 拉取服务端 changes 流。
     *
     * @return [PullResponse]，包含 changes + cursor（服务端最新 cursor）。
     * @throws SyncHttpException 非 2xx 响应。
     * @throws java.io.IOException 传输层失败。
     */
    suspend fun pull(request: PullRequest): PullResponse

    /**
     * POST /sync/bootstrap — 拉取用户完整数据快照 + checksum。
     *
     * T-M3-03: 用于设备重建。返回的 checksum 是服务端对所有实体的确定性
     * 哈希，客户端需重新计算并对比，不一致表示重建失败。
     *
     * @return [BootstrapResponse]，包含完整实体列表 + cursor + checksum。
     * @throws SyncHttpException 非 2xx 响应。
     * @throws java.io.IOException 传输层失败。
     */
    suspend fun bootstrap(): BootstrapResponse
}

// ─── Requests ───

/** Push 请求体。 */
data class PushRequest(
    /** 待推送 mutation 列表（已按 createdAt 升序）。 */
    val mutations: List<MutationWire>,
    /** 当前 client cursor（服务端用来识别 bootstrap 场景）。 */
    val cursor: Long,
    /** T-M5-05ab S2-5: 推送前未同步 mutation 数，供服务端聚合计算 sync_queue_depth Gauge。 */
    val pendingCount: Int? = null
)

/**
 * 单条 mutation 的 wire 形态（camelCase，与服务端契约一致）。
 *
 * 字段说明详见 docs/architecture/overview.md §sync-payload。
 *
 * `payload` 是 camelCase JSON 字符串（按 entityType 决定 schema）。
 * `expectedVersion` 为 null 表示 insert；非 null 表示 update/delete 的期望版本。
 */
data class MutationWire(
    val mutationId: String,
    val entityType: String,
    val entityId: String,
    val operation: String,  // "insert" | "update" | "delete"
    val expectedVersion: Int?,
    val payload: String
)

/** Pull 请求体。 */
data class PullRequest(
    val cursor: Long,
    val limit: Int = 200
) {
    companion object {
        /** N-M3-11 修复：客户端防御性上限 — 服务端 pull limit 最大 500，此处设为同值作纵深防御。 */
        const val MAX_CHANGES = 500
    }
}

// ─── Responses ───

/** Push 响应。 */
data class PushResponse(
    val acceptedMutationIds: List<String>,
    /** 服务端拒绝的 mutation（expected_version 冲突 / 业务失败）。 */
    val rejected: List<RejectedMutation>,
    /** 服务端建议的 cursor（用于下一轮 pull 基准）。 */
    val nextCursor: Long
)

data class RejectedMutation(
    val mutationId: String,
    /** 失败原因：`expected_version_mismatch` | `not_found` | `forbidden` | `invalid_payload`。 */
    val reason: String
)

/** Pull 响应。 */
data class PullResponse(
    val changes: List<ServerChange>,
    val nextCursor: Long
)

/**
 * T-M3-03: Bootstrap 响应 — 完整数据快照 + checksum。
 *
 * `entities` 中的每个 Map 是 camelCase 字段的 entity payload（与服务端 pull
 * 返回的 entity 结构一致，已在 SyncPayloadMapper 中完成 snake→camel 映射）。
 *
 * @property checksum 服务端声明的 SHA-256 checksum（小写 64 字符十六进制），
 *   由服务端用 snake_case 原始键计算（见 server/.../service.py:compute_bootstrap_checksum）。
 * @property computedChecksum 客户端在解析 JSON 时立即用 snake_case 原始键重算的
 *   checksum，与 [checksum] 必须一致；不一致表示 snake/camel 映射或类型转换丢失字段。
 *   P0-1 修复：此前客户端对 camelCase Map 重算，因键名不同永远无法匹配。
 */
data class BootstrapResponse(
    val books: List<Map<String, String?>>,
    val categories: List<Map<String, String?>>,
    val accounts: List<Map<String, String?>>,
    val transactions: List<Map<String, String?>>,
    val budgets: List<Map<String, String?>>,
    val cursor: Long,
    val checksum: String,
    val computedChecksum: String = ""
)

/** HTTP 错误；包含状态码与响应体摘要。 */
class SyncHttpException(val httpCode: Int, message: String) :
    java.io.IOException("sync HTTP $httpCode: $message")
