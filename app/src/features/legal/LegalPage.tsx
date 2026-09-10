/**
 * LegalPage — 法律文本展示页（隐私政策 / 用户协议）。
 *
 * T-M5-04a F13: OnboardingPage 中"隐私政策"/"用户协议"链接的实际落点。
 * 内容内联以保证 WebView 离线可读；与 docs/legal/privacy-policy-v1.md 保持一致。
 */

import { PageHeader } from '../../components/Layout'

const PRIVACY_SECTIONS: Array<{ title: string; body: string[] }> = [
  {
    title: '1. 概述',
    body: ['Aozora（"我们"）尊重你的隐私。本政策说明我们收集哪些数据、如何使用、以及你的权利。'],
  },
  {
    title: '2. 数据采集范围',
    body: [
      '设备信息：设备标识符（device_id，用于认证与会话）、时区（用于日期显示）。',
      '业务数据：交易、账本、分类、账户、预算；你主动上传的附件图片（可选）；你与 AI 助手的交互（可选）。',
      '我们不收集：通讯录、短信、通话记录、位置、其他应用使用数据。',
    ],
  },
  {
    title: '3. 数据存储',
    body: [
      '所有业务数据首先存储在你的设备上（本地数据库）。未登录也可使用核心记账功能。',
      '仅在你明确同意本政策后，数据才会同步到云端；传输使用 HTTPS/TLS 加密；密码以 bcrypt 哈希存储。',
      '账号存续期间持续保留；账号删除后 7 天冻结期内可恢复，之后物理删除全部数据；附件原图解析后 24 小时自动删除。',
    ],
  },
  {
    title: '4. AI 使用说明',
    body: [
      'AI 助手（"小贝"）由第三方大语言模型驱动，记账数据在处理时会发送至 AI 服务商。',
      'AI 生成的内容旁会标注 "AI" 标识；AI 可能出错，重要决策请自行核实。',
    ],
  },
  {
    title: '5. 第三方共享',
    body: [
      '未同意隐私政策前，无业务数据上行。',
      '仅 AI 功能涉及第三方模型调用；我们不发售你的任何数据。',
    ],
  },
  {
    title: '6. 你的权利',
    body: [
      '数据导出：在"我的"页面可随时导出全部数据为 JSON/CSV 文件。',
      '账号删除：在"我的"页面可删除账号，经二次确认后 7 天物理删除。',
      '权限管理：相机 / 麦克风 / 通知均在首次使用时单独申请；拒绝任何权限不影响手动记账。',
    ],
  },
  {
    title: '7. 联系方式',
    body: ['如有隐私相关问题，请通过项目仓库的 Issue 与我们联系。'],
  },
]

const TERMS_SECTIONS: Array<{ title: string; body: string[] }> = [
  {
    title: '1. 服务说明',
    body: ['Aozora 是一款个人记账工具，提供本地优先、可选云端同步的记账服务。'],
  },
  {
    title: '2. 账号与设备',
    body: [
      '你可以以游客身份使用核心功能，也可以注册账号以启用跨设备同步。',
      '账号与设备标识绑定；请勿将账号分享给他人。',
    ],
  },
  {
    title: '3. 可接受使用',
    body: [
      '你不得使用本服务从事违法活动，或干扰、破坏服务本身。',
      'AI 助手输出仅供参考，不构成任何财务或专业建议。',
    ],
  },
  {
    title: '4. 责任边界',
    body: [
      '本服务按"现状"提供，不对可用性、准确性作明示或暗示的保证。',
      '因你自身操作导致的数据损失由你承担；我们因故意或重大过失导致的损失除外。',
    ],
  },
  {
    title: '5. 变更与终止',
    body: [
      '条款或隐私政策变更时，我们会在产品内提示你重新确认。',
      '你可以随时删除账号与全部数据。',
    ],
  },
]

export function PrivacyPage() {
  return <LegalPage title="隐私政策" version="v1.0 — 2026-09-07" sections={PRIVACY_SECTIONS} />
}

export function TermsPage() {
  return <LegalPage title="用户协议" version="v1.0 — 2026-09-07" sections={TERMS_SECTIONS} />
}

function LegalPage({
  title,
  version,
  sections,
}: {
  title: string
  version: string
  sections: Array<{ title: string; body: string[] }>
}) {
  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)' }}>
      <PageHeader back />
      <div className="p-6">
        <div className="max-w-2xl mx-auto">
          <div className="mb-6">
            <h1 className="text-base font-semibold" style={{ color: 'var(--ink)' }}>
              {title}
            </h1>
            <div className="text-xs text-ink2 mt-1">{version}</div>
          </div>

          <div className="space-y-5">
            {sections.map((s) => (
              <section key={s.title}>
                <h2
                  className="text-base font-semibold mb-2"
                  style={{ color: 'var(--ink)' }}
                >
                  {s.title}
                </h2>
                <div className="space-y-2">
                  {s.body.map((p, i) => (
                    <p
                      key={i}
                      className="text-sm leading-relaxed"
                      style={{ color: 'var(--ink2)' }}
                    >
                      {p}
                    </p>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
