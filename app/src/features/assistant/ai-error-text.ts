/**
 * T18-1（REQ-66）：AI 链路**用户可见**错误文案的单一来源。
 *
 * 归口前，同一条桥命令（`image.parse` → `POST /ai/parse-image`）在两条链路上
 * 各写了一套 503 说法：聊天页说「AI 服务未配置：需在服务器 .env 填
 * AI_PROVIDER_API_KEY 并重启」，快捷记账说「识别失败（AI 服务不可用）」。
 * 同一故障两种说法，且前者把服务端运维细节（env 文件名 + 变量名）透给了用户
 * ——而那个码（`no_ai_provider`）是**桥的误译**：服务端从不下发它。
 *
 * 口径（APP-CONTEXT §6.4「不暴露服务商错误」）：文案只回答「现在能不能用、
 * 要不要重试」，不含 provider 名、key 名、配置路径、HTTP 状态码。
 * **归因分层由错误码承载**（服务端 body 的 code → 桥码透传，见
 * `AozoraBridge.handleImageParse`），不靠文案区分：用户不需要消化「是哪一种 503」。
 */

/** 服务端 503（AI 服务不可用）的统一用户可见文案，两条链路共用。 */
export const AI_UNAVAILABLE_ZH = 'AI 服务暂时不可用，请稍后重试'

/**
 * 「AI 暂时不可用」类错误码——文案归一的**判据**。
 *
 * - `ai_unavailable`：服务端 503 的主码（provider 暂时故障 / 未配置 —— 服务端
 *   对这两者同码，API 层不可分）。
 * - `image_parsing_disabled`：服务端 503 的另一个子码（`AI_IMAGE_MAX_BYTES=0`，
 *   运维关闭了图片解析）。
 * - `no_ai_provider`：T18-1 之前桥把**任意** 503 一律压成的兜底码，现仅作兼容保留。
 * - `server_error` / `503`：非 503 的 `Unavailable` 与旧映射键。
 */
export const AI_UNAVAILABLE_CODES: ReadonlySet<string> = new Set([
  'ai_unavailable',
  'image_parsing_disabled',
  'no_ai_provider',
  'server_error',
  '503',
])

/** 该桥错误码是否属于「AI 暂时不可用」类（两条链路共用同一判据）。 */
export function isAiUnavailable(code: string | null | undefined): boolean {
  return !!code && AI_UNAVAILABLE_CODES.has(code)
}

/**
 * T20-9（REQ-82）：**服务端明确拒绝**类错误码——它与「链路断了」是两件事。
 *
 * 这组码以前到达聊天页时一律落 `INTERRUPTED_TURN_NOTE`（「连接中断，本轮未完成」）：
 * 那句对**真断连**是对的，对这里却是谎——请求明明到了服务端、服务端也答了 429，
 * 而用户的下一步动作完全不同（等额度恢复 / 缓一缓再发，不是查网络再重试）。
 * 成因是两层翻译丢信息：Kotlin 只看状态码 → 桥硬编码文案 → 前端统一兜底。
 * T20-9 把服务端的 429 子码透出来（`AozoraBridge.rateLimitedCodeAndMessage`），
 * 这里据此给出**如实**的一段话。
 */
export const AI_QUOTA_EXCEEDED_CODE = 'ai_quota_exceeded'

/**
 * 拒绝类码 → 聊天气泡文案。三个限流键是**同一个语义的三种码**：`429` 是桥在认不出
 * 子码时的笼统码，`rate_limited` 是图片链路的既有码，`ai_rate_limited` 是服务端
 * 自己的码（桥当前不产出，留着是为了「码先于接线」时不用再来改一次）。
 */
const AI_REFUSAL_ZH: Record<string, string> = {
  [AI_QUOTA_EXCEEDED_CODE]: '⚠️ 今天的 AI 额度用完了，明天再试试',
  '429': '⚠️ AI 请求太频繁了，缓一会儿再发',
  rate_limited: '⚠️ AI 请求太频繁了，缓一会儿再发',
  ai_rate_limited: '⚠️ AI 请求太频繁了，缓一会儿再发',
}

/**
 * 该错误码对应的**拒绝文案**；`null` = 不是这一类，调用方照旧用
 * `INTERRUPTED_TURN_NOTE`（真断连、超时、网络故障都归那句）。
 */
export function aiRefusalNote(code: string | null | undefined): string | null {
  return (code && AI_REFUSAL_ZH[code]) || null
}

/**
 * 图片（小票）解析链路：桥错误码 → 中文文案。未知码由调用方回退原生 message。
 *
 * T12-1（REQ-29 / 决策 #9）：本表最初只有 `no_ai_provider` 一条「配置指引」
 * 文案；T18-1 起该类一律落到 {@link AI_UNAVAILABLE_ZH}。
 * T14-3（REQ-40）：补 `execute` 分支的码集（`network`/`429`/`503`）与服务端
 * 400 `ai_missing_category`、500 `database_error` 防御键。
 */
export const IMAGE_ERROR_ZH: Record<string, string> = {
  ai_unavailable: AI_UNAVAILABLE_ZH,
  image_parsing_disabled: AI_UNAVAILABLE_ZH,
  no_ai_provider: AI_UNAVAILABLE_ZH,
  server_error: AI_UNAVAILABLE_ZH,
  '503': AI_UNAVAILABLE_ZH,
  timeout: '图片解析超时，请检查网络后重试',
  network_error: '网络错误，请检查网络连接后重试',
  network: '网络错误，请检查网络后重试',
  rate_limited: 'AI 请求过于频繁，请稍后重试',
  '429': 'AI 请求过于频繁，请稍后重试',
  auth_lost: '登录已失效，请重新登录',
  ai_missing_category: '有记录未识别到分类，请在卡片中选择分类后重试',
  database_error: '服务处理失败，请稍后重试',
}