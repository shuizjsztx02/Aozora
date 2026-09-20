package com.aozora.app.sync

import android.content.Context
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.SyncMetaEntity
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T-M7-05j：bootstrap 的**偏好项车道** —— checksum 车道选择（v3 > v2 > legacy）
 * 与实体计数校验（含新的 `user_preferences` 类）。
 *
 * 车道背景：服务端同时下发 v2（八类）与 v3（九类）。v2 的输入**刻意不含偏好**，
 * 否则 05j 之前构建的客户端会在下次 bootstrap 判失配 → RETRYABLE 死循环。
 * 故新客户端必须优先用 v3；连到 05j 之前的服务端（无 checksum_v3）才退回 v2。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SyncEnginePreferenceBootstrapTest {

    /** 只提供 bootstrap 的 SyncApi 替身（本文件不涉 push/pull）。 */
    private class FakeApi(
        var response: BootstrapResponse? = null,
        var rawJson: String? = null
    ) : SyncApi {
        override suspend fun push(request: PushRequest): PushResponse =
            throw IllegalStateException("push not expected")

        override suspend fun pull(request: PullRequest): PullResponse =
            throw IllegalStateException("pull not expected")

        override suspend fun bootstrap(): BootstrapResponse =
            rawJson?.let { SyncPayloadMapper.parseBootstrapResponse(it) }
                ?: response ?: throw IllegalStateException("no bootstrap behavior configured")
    }

    private class StubAuth(context: Context) : AuthClient(
        baseUrl = "http://fake",
        tokenStore = TokenStore(context),
        httpClient = OkHttpClient()
    ) {
        override suspend fun ensureValidAccessToken(): String = "fake-token"
        override fun currentUserId(): String? = "u1"
        override fun clearAuth() {}
    }

    private lateinit var db: AozoraDatabase
    private lateinit var repo: Repository
    private lateinit var engine: SyncEngine

    @Before
    fun setUp() {
        val ctx = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(ctx)
        repo = Repository(db)
    }

    @After
    fun tearDown() { db.close() }

    private fun buildEngine(api: FakeApi): SyncEngine =
        SyncEngine(repo, api, StubAuth(RuntimeEnvironment.getApplication()))

    /** 与 Python 参考脚本同形的响应：一条 default_book_id 偏好 + 三个 checksum。 */
    private fun bootstrapJsonWithPreference(
        v2Override: String? = null,
        v3Override: String? = null
    ): String = """
        {
          "books": [], "categories": [], "accounts": [], "transactions": [], "budgets": [],
          "savings_goals": [], "scheduled_rules": [], "chat_personas": [],
          "user_preferences": [
            {"id": "44444444-4444-4444-8444-111111111111", "key": "default_book_id",
             "value": "77777777-7777-4777-8777-111111111111", "version": 1}
          ],
          "cursor": 42,
          "checksum": "2cfcc3208403af4dde547253e4e0d23cf4e3ec1a5b90ef64caffe101020ef94e",
          "checksum_v2": "${v2Override ?: V2_REFERENCE}",
          "checksum_v3": "${v3Override ?: V3_REFERENCE}"
        }
    """.trimIndent()

    /**
     * 真实 JSON 路径（snake_case → parseBootstrapResponse → SyncEngine）：v3 校验通过，
     * 偏好行落 Room，值回灌 sync_meta。
     */
    @Test
    fun bootstrap_v3ChecksumVerifies_andPreferenceLandsInMeta() = runBlocking {
        val outcome = buildEngine(FakeApi(rawJson = bootstrapJsonWithPreference()))
            .runBootstrapForced()

        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        // 值回灌 sync_meta —— "AI 改了默认账本 → 本机 UI 跟着变"的落点
        assertEquals(
            "77777777-7777-4777-8777-111111111111",
            repo.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID),
        )
        val row = db.userPreferenceDao().getByKey(SyncMetaEntity.KEY_DEFAULT_BOOK_ID)
        assertNotNull(row)
        assertEquals("44444444-4444-4444-8444-111111111111", row!!.id)
        assertEquals(42L, repo.getSyncCursor())
    }

    /**
     * 车道优先序：v3 在场时**不得**去看 v2 —— 把 v2 的声明值改坏，结果仍须 OK。
     * （若实现退回 v2，这里必然 RETRYABLE。）
     */
    @Test
    fun bootstrap_prefersV3OverV2() = runBlocking {
        val brokenV2 = bootstrapJsonWithPreference(v2Override = "0".repeat(64))
        val outcome = buildEngine(FakeApi(rawJson = brokenV2)).runBootstrapForced()
        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
    }

    @Test
    fun bootstrap_corruptV3_returnsRetryable() = runBlocking {
        val outcome = buildEngine(
            FakeApi(rawJson = bootstrapJsonWithPreference(v3Override = "0".repeat(64)))
        ).runBootstrapForced()
        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
    }

    /** 旧服务端（无 checksum_v3 / 无 user_preferences）仍走 v2 车道并通过。 */
    @Test
    fun bootstrap_withoutV3_fallsBackToV2() = runBlocking {
        // 空数据 + cursor=7 时五类/八类/九类同值：sha256("cursor:7")
        val v2 = BootstrapChecksum.computeV2(
            emptyList(), emptyList(), emptyList(), emptyList(), emptyList(),
            emptyList(), emptyList(), emptyList(), 7L,
        )
        val json = """
            {"books": [], "categories": [], "accounts": [], "transactions": [],
             "budgets": [], "cursor": 7, "checksum": "x", "checksum_v2": "$v2"}
        """.trimIndent()
        val outcome = buildEngine(FakeApi(rawJson = json)).runBootstrapForced()
        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        assertEquals(7L, repo.getSyncCursor())
    }

    /**
     * 计数校验必须覆盖新的 user_preferences 类：响应声明 1 条偏好、但该行缺 key
     * （进入 Room 前被 skip）→ 本地 0 条 → RETRYABLE。checksum 仍匹配，故这条
     * 只能靠计数校验兜住。
     */
    @Test
    fun bootstrap_preferenceCountMismatch_returnsRetryable() = runBlocking {
        val declaredPrefs = listOf(
            mapOf("id" to "p1", "key" to "default_book_id", "value" to "b1", "version" to "1"),
            // 缺 key → serverChangeMapToUserPreference 返回 null → skip
            mapOf("id" to "p2", "value" to "b2", "version" to "1"),
        )
        val snakePrefs = listOf(
            mapOf("id" to "p1", "key" to "default_book_id", "value" to "b1", "version" to 1),
            mapOf("id" to "p2", "value" to "b2", "version" to 1),
        )
        val checksum = BootstrapChecksum.computeV3(
            emptyList(), emptyList(), emptyList(), emptyList(), emptyList(),
            emptyList(), emptyList(), emptyList(), snakePrefs, 3L,
        )
        val response = BootstrapResponse(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(), cursor = 3L,
            checksum = "", computedChecksum = "",
            userPreferences = declaredPrefs, checksumV3 = checksum,
            computedChecksumV3 = checksum,
        )
        val outcome = buildEngine(FakeApi(response = response)).runBootstrapForced()

        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
    }

    /** 计数一致时同样通过（对照组：证明上一条红是因为计数而非别的）。 */
    @Test
    fun bootstrap_preferenceCountMatch_succeeds() = runBlocking {
        val prefs = listOf(
            mapOf("id" to "p1", "key" to "default_book_id", "value" to "b1", "version" to "1")
        )
        val checksum = BootstrapChecksum.computeV3(
            emptyList(), emptyList(), emptyList(), emptyList(), emptyList(),
            emptyList(), emptyList(), emptyList(),
            listOf(mapOf("id" to "p1", "key" to "default_book_id", "value" to "b1", "version" to 1)),
            3L,
        )
        val response = BootstrapResponse(
            books = emptyList(), categories = emptyList(), accounts = emptyList(),
            transactions = emptyList(), budgets = emptyList(), cursor = 3L,
            checksum = "", computedChecksum = "",
            userPreferences = prefs, checksumV3 = checksum, computedChecksumV3 = checksum,
        )
        val outcome = buildEngine(FakeApi(response = response)).runBootstrapForced()
        assertEquals(SyncEngine.SyncOutcome.OK, outcome)
        assertEquals("b1", repo.getMeta(SyncMetaEntity.KEY_DEFAULT_BOOK_ID))
    }

    private companion object {
        /** Python compute_bootstrap_checksum_v3(上述九类输入, cursor=42) 的真实输出。 */
        const val V3_REFERENCE = "217cbdd5f5bdccea080d6356bc7419660e587bb929aaf0edbecefdd095d8cd55"
        const val V2_REFERENCE = "2cfcc3208403af4dde547253e4e0d23cf4e3ec1a5b90ef64caffe101020ef94e"
    }
}