package com.aozora.app.autobook

import android.accessibilityservice.AccessibilityService
import android.content.pm.PackageManager
import android.view.accessibility.AccessibilityEvent
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

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val e = event ?: return
        // 自己 app 的事件绝不进采集面（设置页、首页等）
        val pkg = e.packageName?.toString().orEmpty()
        if (pkg.isEmpty() || pkg == packageName) return

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
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> handleContentChanged(e, pkg)
            // 其他事件类型本服务不订阅，配置 XML 已限定；即便收到也直接忽略
        }
    }

    private fun handleContentChanged(e: AccessibilityEvent, pkg: String) {
        // 没有当前页面 key（服务刚启动，尚未收到 STATE_CHANGED）→ 跳过
        val pageKey = currentPageKey.ifEmpty { return }

        // 拼文本：按行拼出页面所有可见文字。AccessibilityEvent.getText() 已经按节点
        // 顺序给出列表；转 String 时空 CharSequence 跳过，避免"null"字符串污染
        val composedText = composeText(e)
        if (composedText.isEmpty()) return

        // 内存签名防抖：归一化文本 hash 与上次相同 → 同一页面抖动，跳过
        val signature = DedupeKeys.sha256Hex(DedupeKeys.normalizeText(composedText))
        if (pageSignatures.put(pageKey, signature) == signature) return

        scope.launch {
            if (!repository.isEnabled()) return@launch
            val sources = repository.sourcePackages()
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
                    postedAt = e.eventTime.takeIf { it > 0 } ?: System.currentTimeMillis(),
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
     * 把 [AccessibilityEvent] 的文本列表按行拼成一段。**空段跳过**，与通知侧的
     * [NotificationFilter.composeText] 同口径——重复段会让 L1 键跟着变，凭空造出两行。
     */
    private fun composeText(e: AccessibilityEvent): String {
        val list = e.text ?: return ""
        if (list.isEmpty()) return ""
        val parts = list.mapNotNull { it?.trim()?.takeIf { s -> s.isNotEmpty() } }
            .distinct()
        return parts.joinToString("\n")
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
}
