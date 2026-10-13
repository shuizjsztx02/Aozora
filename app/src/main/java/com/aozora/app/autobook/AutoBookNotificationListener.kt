package com.aozora.app.autobook

import android.app.Notification
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import com.aozora.app.AozoraApp
import com.aozora.app.db.entity.AutoCaptureEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * 通知通道（第二十一轮 REQ-83 / T21-3，ADR-016 决策 1）。
 *
 * `onNotificationPosted` **只做预筛 + 落库**——不做网络、不做解析（决策 #4）。
 * 对齐本仓"网络不进后台服务"的既有取向，也避免在系统回调里阻塞。
 *
 * 授权口径：**不是运行时权限**。本服务靠 manifest 里的 `<service>` +
 * `android.permission.BIND_NOTIFICATION_LISTENER_SERVICE` 声明，用户在系统
 * 「通知使用权」页授予（`<uses-permission>` 一条都不加，决策 #3）。判定与引导
 * 由 T21-7 经 `PermissionManager` 的「设置页授权」形态给出。
 *
 * 两道闸门（缺一不可，顺序即代价从低到高）：
 * 1. **总开关**：默认关闭（决策 #9）——未开启时连判定都不做；
 * 2. **来源集合**：内置三项 ∪ 用户勾选（[AutoBookRepository.sourcePackages]）。
 *
 * 原文是**敏感数据**：只进 `auto_captures` 表，**不写任何日志**（`security.md`）。
 */
class AutoBookNotificationListener : NotificationListenerService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val repository: AutoBookRepository by lazy {
        AutoBookRepository((application as AozoraApp).database)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val posted = sbn ?: return
        // 自己发的通知（每日提醒、T21-7 的"已记一笔"回执）绝不进采集面
        if (posted.packageName == packageName) return
        // 正在进行的通知（下载、播放、导航）不是支付事件
        if (posted.isOngoing) return

        val extras = posted.notification?.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
        val bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()

        scope.launch {
            if (!repository.isEnabled()) return@launch
            val decision = NotificationFilter.evaluate(
                sourcePackage = posted.packageName,
                title = title,
                text = text,
                bigText = bigText,
                sourcePackages = repository.sourcePackages(),
            )
            // 非支付类当场丢弃：不落库、不上行（决策 #13 的隐私兑现点）
            if (!decision.accepted) return@launch
            repository.recordCapture(
                AutoBookRepository.CaptureDraft(
                    sourceChannel = AutoCaptureEntity.CHANNEL_NOTIFICATION,
                    sourcePackage = posted.packageName,
                    sourceApp = appLabel(posted.packageName),
                    rawText = NotificationFilter.composeText(title, text, bigText),
                    postedAt = posted.postTime,
                    eventIdentity = posted.key,
                )
            )
        }
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