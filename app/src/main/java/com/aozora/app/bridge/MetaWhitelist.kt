package com.aozora.app.bridge

import com.aozora.app.db.entity.SyncMetaEntity
import org.json.JSONArray
import org.json.JSONException

/**
 * T9-5a（REQ-11 / 决策 #1）：meta.get / meta.set 的键白名单与值格式校验。
 *
 * 纯函数对象（不依赖 WebView），供 AozoraBridge handler 调用并可在 JVM 单测。
 * 存储一律 sync_meta：不加实体列（防 bootstrap 冲刷）、不上行（不污染 push 契约）。
 * 非法输入统一抛 IllegalArgumentException → 桥统一转 invalid_input 错误响应。
 */
object MetaWhitelist {

    /** 可经 meta.* 读写的键；master_book_id 由种子逻辑自管，不开放。 */
    val KEYS = setOf(
        SyncMetaEntity.KEY_DEFAULT_BOOK_ID,
        SyncMetaEntity.KEY_DEFAULT_ACCOUNT_ID,
        SyncMetaEntity.KEY_BOOK_ORDER,
        SyncMetaEntity.KEY_ACCOUNT_ORDER,
        // 第十轮 T10-3（决策 #4）：账号展示信息（仅本地，不上行）。
        SyncMetaEntity.KEY_AVATAR_URI,
        SyncMetaEntity.KEY_DISPLAY_NAME,
        // 第十一轮 T11-1（REQ-23 / 决策 #1）：预算沿用。mark 仅 Repository 自管写入，
        // 开放读取与前端应急修正，故一并入白名单。
        SyncMetaEntity.KEY_BUDGET_CARRYOVER,
        SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK,
        // 第十一轮 T11-2（REQ-24 / 决策 #5）：桌面小组件全局配置（所有实例共享一份）。
        SyncMetaEntity.KEY_WIDGET_CONFIG,
        // 第十一轮 T11-6（REQ-27 / 决策 #7）：用户画像（AI context 注入源）。
        SyncMetaEntity.KEY_USER_PROFILE,
        // 第十一轮 T11-7（REQ-28 / 决策 #8）：当前聊天角色 id（UUID；空=清除回退"小贝"）。
        // personas_seeded 为 Repository 自管键（镜像 master_book_id 不开放前端写）。
        SyncMetaEntity.KEY_ACTIVE_PERSONA_ID,
    )

    private const val MAX_VALUE_LEN = 8192
    /** widget_config 上限（决策 #1：≤4KB；四开关+点击行为远小于此，纯防御）。 */
    private const val MAX_WIDGET_CONFIG_LEN = 4096
    /** avatar_uri 特例：icon.pick 压缩链（128px/q75）产物 data URI 可达 ~48KB（决策 #4）。 */
    private const val MAX_AVATAR_LEN = 64 * 1024
    /** display_name 与服务端 users.nickname String(64) 同限。 */
    private const val MAX_DISPLAY_NAME_LEN = 64

    /** id 形态 = UUID v4（与服务端 sync 契约同形；不校验实体存在性，读取侧容错）。 */
    val ID_RE = Regex(
        "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
    )

    fun isAllowed(key: String): Boolean = KEYS.contains(key)

    /** 校验 meta.set 的 value；非法抛 IllegalArgumentException，合法静默返回。 */
    fun validateValue(key: String, value: String) {
        when (key) {
            SyncMetaEntity.KEY_AVATAR_URI -> {
                require(value.length <= MAX_AVATAR_LEN) {
                    "avatar_uri too large (max $MAX_AVATAR_LEN chars)"
                }
                require(value.isEmpty() || value.startsWith("data:")) {
                    "avatar_uri must be a data: URI (or empty to clear)"
                }
            }
            SyncMetaEntity.KEY_DISPLAY_NAME -> {
                require(value.length <= MAX_DISPLAY_NAME_LEN) {
                    "display_name too long (max $MAX_DISPLAY_NAME_LEN chars)"
                }
            }
            SyncMetaEntity.KEY_BUDGET_CARRYOVER -> {
                require(value == "1" || value == "0") { "budget_carryover must be '1' or '0'" }
            }
            SyncMetaEntity.KEY_BUDGET_CARRYOVER_MARK -> {
                require(value.length <= MAX_VALUE_LEN) {
                    "meta value too large (max $MAX_VALUE_LEN chars)"
                }
                val obj = try {
                    org.json.JSONObject(value)
                } catch (e: JSONException) {
                    throw IllegalArgumentException("budget_carryover_mark must be a JSON object")
                }
                val monthRe = Regex("\\d{4}-\\d{2}")
                for (k in obj.keys()) {
                    require(monthRe.matches(obj.getString(k))) {
                        "budget_carryover_mark values must be 'YYYY-MM'"
                    }
                }
            }
            SyncMetaEntity.KEY_WIDGET_CONFIG -> {
                require(value.length <= MAX_WIDGET_CONFIG_LEN) {
                    "widget_config too large (max $MAX_WIDGET_CONFIG_LEN chars)"
                }
                if (value.isNotEmpty()) {
                    // 空串 = 清除配置（读取端回退全默认）；非空必须是 JSON 对象。
                    // 键级容错（缺键/坏值取默认）在 WidgetConfig.parse，读取端兜底。
                    try {
                        org.json.JSONObject(value)
                    } catch (e: JSONException) {
                        throw IllegalArgumentException("widget_config must be a JSON object")
                    }
                }
            }
            SyncMetaEntity.KEY_USER_PROFILE -> {
                // T11-6（决策 #1/#7）：画像 JSON 对象 ≤8KB；空串 = 清除（注入管线回退无 context 行为）。
                require(value.length <= MAX_VALUE_LEN) {
                    "user_profile too large (max $MAX_VALUE_LEN chars)"
                }
                if (value.isNotEmpty()) {
                    try {
                        org.json.JSONObject(value)
                    } catch (e: JSONException) {
                        throw IllegalArgumentException("user_profile must be a JSON object")
                    }
                }
            }
            SyncMetaEntity.KEY_BOOK_ORDER, SyncMetaEntity.KEY_ACCOUNT_ORDER -> {
                require(value.length <= MAX_VALUE_LEN) {
                    "meta value too large (max $MAX_VALUE_LEN chars)"
                }
                val arr = try {
                    JSONArray(value)
                } catch (e: JSONException) {
                    throw IllegalArgumentException("$key must be a JSON string array")
                }
                for (i in 0 until arr.length()) {
                    val id = arr.optString(i, null)
                    require(id != null && ID_RE.matches(id)) { "$key elements must be UUID strings" }
                }
            }
            else -> {
                require(value.length <= MAX_VALUE_LEN) {
                    "meta value too large (max $MAX_VALUE_LEN chars)"
                }
                // default_* 键：UUID 形态；空串 = 清除默认
                require(value.isEmpty() || ID_RE.matches(value)) { "$key must be a UUID string" }
            }
        }
    }
}
