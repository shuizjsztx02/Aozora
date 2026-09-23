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