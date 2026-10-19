package com.aozora.app.autobook

import android.accessibilityservice.AccessibilityService
import android.content.pm.PackageManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.aozora.app.AozoraApp
import com.aozora.app.db.entity.AutoCaptureEntity
import com.aozora.app.sync.SyncScheduler
import com.aozora.app.widget.AozoraWidgetProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap

/**
 * 无障碍通道（第二十一轮 REQ-83 / T21-4，ADR-016 决策 1 / 3）。
 *
 * **只做预筛 + 落库**——不做网络、不做解析（决策 #4）。对齐本仓"网络不进后台服务"
 * 的既有取向，也避免在系统回调里阻塞。
 *
 * ★★★ **只读不写**：本服务**不调用任何 `performAction`**（`ACTION_CLICK` /
 * `ACTION_SET_TEXT` / 手势派发一律禁止）。这是"读屏记账"与"AI 智能体操作第三方
 * App"的分界线（ADR-016 决策 3）。配套守护三处：
 *   1. 配置 XML `res/xml/accessibility_service_config.xml` 不声明 `canPerformGestures`；
 *   2. [AutoBookAccessibilityServiceTest] 里的 `serviceSourceCode_doesNotCallPerformAction`
 *      做 grep 式反向断言；
 *   3. [SecurityConfigTest] 的 `manifest_declaresAccessibilityServiceWithBindPermission`
 *      守住 manifest 声明。
 *
 * **事件订阅**：只收 `TYPE_WINDOW_STATE_CHANGED`（进入新页面）与
 * `TYPE_WINDOW_CONTENT_CHANGED`（页面内容变化）。**不订阅按键、触摸、通知**——
 * 配置 XML 里 `accessibilityEventTypes` 已声明，由系统强制。
 *
 * **两道闸门**（缺一不可，顺序即代价从低到高，与通知侧同形态）：
 *   1. **总开关**：默认关闭（决策 #9）——未开启时连判定都不做；
 *   2. **来源集合**：内置三项 ∪ 用户勾选（[AutoBookRepository.sourcePackages]）。
 *
 * **两层防抖**（`TYPE_WINDOW_CONTENT_CHANGED` 在页面上高频触发，每次 UI 刷新都回调）：
 *   1. **内存签名**（本对象内）：`pkg + activityClass → hash(归一化文本)`，文本没变就
 *      直接跳过，**根本不走到 DB**；
 *   2. **L1 去重**（[DedupeKeys] + Room 唯一索引）：即使同一页面的文本**微变**
 *      （秒级刷新导致金额显示变化），L1 的分钟桶 + 归一化文本仍能把它合并为同一行。
 *   两层共同挡住抖动：内存层挡 99% 的重复，DB 层兜住跨进程重启的残差。
 *
 * 原文是**敏感数据**：只进 `auto_captures` 表，**不写任何日志**（`security.md`）。
 */
class AutoBookAccessibilityService : AccessibilityService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val repository: AutoBookRepository by lazy {
        AutoBookRepository((application as AozoraApp).database)
    }

    /** 当前前台页面的 key：`pkg|activityClass`，由 `TYPE_WINDOW_STATE_CHANGED` 维护。 */
    @Volatile
    private var currentPageKey: String = ""

    /**
     * 内存签名：`currentPageKey → hash(归一化文本)`。同一页面文本没变就直接跳过，
     * 不走到 DB。ConcurrentHashMap 因为系统可能从多线程回调（虽然实际单线程，防御性选择）。
     */
    private val pageSignatures = ConcurrentHashMap<String, String>()

    /**
     * 来源集合的内存快照。
     *
     * ★ 存在的理由：决策 #13 要求**不在来源集合里的页面「连文本都不拼装」**，而拼文本
     * 发生在系统回调（主线程、不能 suspend）里，权威集合却在 DB。故在回调里用这份快照
     * 做**前置闸门**（默认只含内置三项，宁可保守），真正的权威判定仍在落库前那次
     * suspend 读取里（见 [handleContentChanged]）——快照过期只会让「用户新加的来源」
     * 晚一拍生效，不会让不该采的包被采。
     */
    @Volatile
    private var cachedSources: Set<String> = NotificationFilter.DEFAULT_SOURCE_PACKAGES

    /** 快照刷新节流：来源集合是低频变更，每 [SOURCE_REFRESH_INTERVAL_MS] 至多读一次。 */
    @Volatile
    private var lastSourceRefreshAt: Long = 0L

    private fun refreshSourcesIfStale() {
        val now = System.currentTimeMillis()
        if (now - lastSourceRefreshAt < SOURCE_REFRESH_INTERVAL_MS) return
        lastSourceRefreshAt = now
        scope.launch {
            cachedSources = runCatching { repository.sourcePackages() }
                .getOrDefault(NotificationFilter.DEFAULT_SOURCE_PACKAGES)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        refreshSourcesIfStale()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val e = event ?: return
        // 自己 app 的事件绝不进采集面（设置页、首页等）
        val pkg = e.packageName?.toString().orEmpty()
        if (pkg.isEmpty() || pkg == packageName) return
        // ★ 来源闸门**前移到拼文本之前**（决策 #13：不在来源集合里的页面「连文本都不拼装」）。
        // 用内存快照判定，因为这里是系统回调、不能 suspend（见 [cachedSources]）。
        if (pkg !in cachedSources) {
            refreshSourcesIfStale()
            return
        }
        when (e.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                // 进入新窗口：记录 key，**清除**旧签名（避免不同页面共用同一 key）。
                // className 可能为 null（系统窗口），用空字符串代替 → 签名仍成立但粒度粗
                val className = e.className?.toString().orEmpty()
                val newKey = "$pkg|$className"
                if (newKey != currentPageKey) {
                    pageSignatures.remove(currentPageKey)
                    currentPageKey = newKey
                }
                // STATE_CHANGED 事件**不落行**：文本往往不全（页面还没渲染完），
                // 真正的文本来自后续 CONTENT_CHANGED 的几次回调
            }
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> handleContentChanged(pkg)
            // 其他事件类型本服务不订阅，配置 XML 已限定；即便收到也直接忽略
        }
    }

    private fun handleContentChanged(pkg: String) {
        // 没有当前页面 key（服务刚启动，尚未收到 STATE_CHANGED）→ 跳过
        val pageKey = currentPageKey.ifEmpty { return }

        // 拼文本：★ **必须遍历窗口节点**，不能只读 `AccessibilityEvent.getText()`。
        // 真机取证（T21-9 收口，Android 16 / Chrome）：`TYPE_WINDOW_CONTENT_CHANGED`
        // 的事件文本**恒为空**（evtLen=0），而 `rootInActiveWindow` 能完整读到页面文字
        // ——只用事件文本会让无障碍通道在真实第三方 App 上**一个字符都拿不到**，
        // 看似"过滤器太严"，实则是通道整体失效。
        val composedText = composeText()
        if (composedText.isEmpty()) return

        // 内存签名防抖：归一化文本 hash 与上次相同 → 同一页面抖动，跳过
        val signature = DedupeKeys.sha256Hex(DedupeKeys.normalizeText(composedText))
        if (pageSignatures.put(pageKey, signature) == signature) return

        scope.launch {
            if (!repository.isEnabled()) return@launch
            val sources = repository.sourcePackages()
            // ★ 来源闸门也在这里再判一次：节点遍历发生在主线程的判定之前，
            // 而来源集合来自 DB（suspend），故这里用权威集合做最终裁决。
            val decision = AccessibilityFilter.evaluate(
                sourcePackage = pkg,
                composedText = composedText,
                sourcePackages = sources,
            )
            // 非支付类当场丢弃：不落库、不上行（决策 #13 的隐私兑现点）
            if (!decision.accepted) return@launch
            val outcome = repository.recordCapture(
                AutoBookRepository.CaptureDraft(
                    sourceChannel = AutoCaptureEntity.CHANNEL_ACCESSIBILITY,
                    sourcePackage = pkg,
                    sourceApp = appLabel(pkg),
                    rawText = composedText,
                    // ★ 必须用 epoch 毫秒，**不能**用 `e.eventTime`：后者是 `uptimeMillis`
                    // 基准（开机以来的毫秒），与通知通道的 `postTime`（epoch）不同源 →
                    // L2 的 mergeKey（含分钟桶）永远对不上，跨通道合并会**静默永不命中**。
                    // 真机取证（T21-9 收口）：eventTime 落库后是 1970-01-01，通知是 2026-10-19。
                    postedAt = System.currentTimeMillis(),
                    // eventIdentity = 当前页面 key：同一页面多次触发 → 同一 L1 键 → 自动丢弃
                    eventIdentity = pageKey,
                )
            )
            if (outcome is AutoBookRepository.CaptureOutcome.Stored) advancePipeline()
        }
    }

    /**
     * 落行后的推进（T21-6）：先跑一次**本地档**编排，再入队既有的同步任务。
     *
     * ★ **本地档不做网络**（`allowRemote = false`）——采集层不做网络是 ADR-016
     * 决策 4 的既有口径；这里只是让本地规则认得出的那一笔**离线也能入账**（PR-3），
     * 未命中 / 字段不完整的行留在 `captured`，由同步任务在联网时跑在线档上行。
     * **不新造调度器**：入队的就是 `SyncScheduler` 那份唯一任务。
     *
     * ★ **只读不写不受影响**：本方法只读本地库、只写本地库与 WorkManager 队列，
     * **没有任何 `performAction`**（见类注释的三处守护）。
     */
    private suspend fun advancePipeline() {
        val app = application as? AozoraApp ?: return
        // 本地入账会改变今日 / 本月聚合，桌面组件跟着刷新（与桥的记账路径同口径）
        if (app.autoBookEngine.processPendingCaptures(allowRemote = false) > 0) {
            AozoraWidgetProvider.requestUpdate(applicationContext)
        }
        SyncScheduler.enqueue(applicationContext)
    }

    /**
     * 遍历当前窗口的节点树，把可见文字**按行**拼成一段。
     *
     * **空段跳过 + 去重**，与通知侧的 [NotificationFilter.composeText] 同口径——重复段
     * 会让 L1 键跟着变，凭空造出两行。
     *
     * ★ 这里**只读**：`rootInActiveWindow` / `getChild` / `text` 都是读操作，
     * **没有任何 `performAction`**（ADR-016 决策 3，由 SecurityConfigTest 的反向
     * 断言钉住）。
     */
    private fun composeText(): String {
        val root = rootInActiveWindow ?: return ""
        val parts = LinkedHashSet<String>()
        collectText(root, 0, parts)
        return parts.joinToString("\n")
    }

    /**
     * 深度优先收集节点文本。[depth] 上限防病态树（真实页面的层级远小于此）。
     * 用 [LinkedHashSet] 去重并保持页面顺序——顺序稳定是 L1 键稳定的前提。
     */
    private fun collectText(node: AccessibilityNodeInfo?, depth: Int, out: MutableSet<String>) {
        if (node == null || depth > MAX_NODE_DEPTH) return
        node.text?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { out.add(it) }
        for (i in 0 until node.childCount) {
            collectText(node.getChild(i), depth + 1, out)
        }
    }

    override fun onInterrupt() {
        // 系统回调（如用户切换输入法 / 弹出系统对话框打断服务）。本服务**只读**，
        // 没有"进行中的操作"需要中断，留空即可。
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /** 应用名只用于展示（待确认箱的来源标注）；取不到就留空，不影响采集。 */
    private fun appLabel(pkg: String): String? = try {
        val pm = packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    } catch (_: PackageManager.NameNotFoundException) {
        null
    }

    private companion object {
        /** 节点树深度上限：真实页面远小于此，纯粹防御病态树。 */
        const val MAX_NODE_DEPTH = 40

        /** 来源集合快照的刷新间隔（低频变更，避免每个事件都读 DB）。 */
        const val SOURCE_REFRESH_INTERVAL_MS = 60_000L
    }
}
