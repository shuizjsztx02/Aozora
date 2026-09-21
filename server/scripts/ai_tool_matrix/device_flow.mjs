// T-M7 全域名 AI 工具测试集 —— **设备端到端**这一腿（模拟器 / CDP）。
//
// 为什么还需要它：服务端执行器（run.py）绕过客户端直接打 `/ai/agent`，量的是**模型层**；
// 而设备上真正决定成败的是三件事，只有真机跑才看得到：
//   ① 本地 NLU 快路径有没有把这句吞掉（`needsAiRouting` 的四判据）；
//   ② v2 车道声明 + 通用确认卡有没有真的渲染出来；
//   ③ 流式步骤卡 / 半截文本 / 错误保留这些客户端形态。
//
// 口径：
//   - 每条用例**先新建一个会话**（点会话列表 → 新建），保证用例之间不串上下文；
//   - 出现 v2 确认卡即点确认（否则只测到「卡出了」，测不到「确认后真的落库」）；
//   - 判据不在这里自动断定，只如实记录 agent 是否被走到、卡有没有出、以及收尾文本。
//
// 用法::
//   node server/scripts/ai_tool_matrix/device_flow.mjs                 # 默认高价值子集
//   node server/scripts/ai_tool_matrix/device_flow.mjs TX-ADD-01 ...  # 指定用例 id
//   CDP_PORT=9222 node server/scripts/ai_tool_matrix/device_flow.mjs
//
// 前置：`adb -s emulator-5554 forward tcp:9222 localabstract:webview_devtools_remote_<pid>`
//       （pid 从 `adb shell cat /proc/net/unix | grep webview_devtools_remote` 取）
import http from 'node:http'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const CORPUS = JSON.parse(readFileSync(resolve(HERE, 'corpus.json'), 'utf-8'))
const PORT = Number(process.env.CDP_PORT || 9222)

/** 默认子集：覆盖三类必须走真机才能验的形态。 */
const DEFAULT_IDS = [
  'TX-ADD-01',      // 记账 → 应走本地快路径（AI 看不到），看它记对没有
  'CMP-01',         // ★ R-PROD-28：复合句的账户/日期会不会被本地吞掉
  'BUD-SETCAT-01',  // v2 写：看确认卡是否渲染、payload 是否正确
  'SAV-UPD-01',     // ★ 服务端「读后不落子」在设备上的表现
  'SCH-ARCH-01',    // ★ 同域近义（update vs archive）在设备上的表现
  'HELP-01',        // ★ R-PROD-39：路由漏注入 → 模型凭自身知识答产品用法
  'SEC-DELETE-01',  // 高风险：确认卡必须出
  'REM-02',         // 设备 op：提醒 + 通知权限
  'NEG-02',         // 能力外请求：应如实拒绝
  'TX-ADD-04',      // ★ 路由漏注入（「卡」把整句路由到账户域）在设备上的表现
]

const wanted = process.argv.slice(2)
const cases = CORPUS.cases.filter(c => (wanted.length ? wanted.includes(c.id) : DEFAULT_IDS.includes(c.id)))

const getJson = (path) => new Promise((res, rej) => {
  http.get({ host: '127.0.0.1', port: PORT, path }, (r) => {
    let b = ''
    r.on('data', c => (b += c))
    r.on('end', () => { try { res(JSON.parse(b)) } catch (e) { rej(new Error(b.slice(0, 200))) } })
  }).on('error', rej)
})

const targets = await getJson('/json/list')
const page = targets.find(t => t.type === 'page')
if (!page) throw new Error('no page target')
const ws = new WebSocket(page.webSocketDebuggerUrl)
const pending = new Map()
let nextId = 1
const send = (m, p) => new Promise(r => { const id = nextId++; pending.set(id, r); ws.send(JSON.stringify({ id, method: m, params: p })) })
ws.addEventListener('message', ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } })
await new Promise(r => ws.addEventListener('open', r))

async function evalInPage(body, timeoutMs = 200000) {
  const wrapped = `(async () => { try { const v = await (async () => { ${body} })(); return JSON.stringify({ok:true, value:v}); } catch (e) { return JSON.stringify({ok:false, error:String(e)}); } })()`
  const res = await Promise.race([
    send('Runtime.evaluate', { expression: wrapped, awaitPromise: true, returnByValue: true }),
    new Promise(r => setTimeout(() => r({ timeout: true }), timeoutMs)),
  ])
  if (res.timeout) return { ok: false, error: 'eval timeout' }
  const r = res.result?.result
  return r ? JSON.parse(r.value) : { ok: false, error: JSON.stringify(res.result) }
}

const sleep = (ms) => `await new Promise(r => setTimeout(r, ${ms}))`

/** 点开会话列表 → 新建 → 关掉抽屉。用例之间必须换会话，否则上下文互相污染。 */
const NEW_CONVERSATION = `
  const openBtn = document.querySelector('[data-testid="conversation-list-button"]')
  if (openBtn) {
    openBtn.click()
    ${sleep(600)}
    const newBtn = document.querySelector('[data-testid="conversation-new"]')
    if (newBtn) { newBtn.click(); ${sleep(700)} }
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }))
    ${sleep(400)}
  }
  return document.querySelectorAll('[data-testid^="msg-"]').length
`

const TURN = (text) => `
  const box = document.querySelector('input[placeholder], textarea[placeholder]')
  if (!box) return 'NO_INPUT'
  const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  set.call(box, ${JSON.stringify(text)})
  box.dispatchEvent(new Event('input', { bubbles: true }))
  ${sleep(400)}
  box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }))

  let sawStep = false, sawV2 = false, v2Payload = '', sawV1 = false, confirmed = false
  const t0 = Date.now(); let last = '', settled = 0
  while (Date.now() - t0 < 90000) {
    await new Promise(r => setTimeout(r, 200))
    if (document.querySelector('[data-testid^="step"], [data-testid*="step-card"]')) sawStep = true
    const v2btn = document.querySelector('[data-testid="v2-card-confirm"]')
    if (v2btn) {
      sawV2 = true
      if (!v2Payload) {
        const card = v2btn.closest('[data-testid="v2-confirmation-card"]') || v2btn.parentElement
        v2Payload = (card?.innerText || '').replace(/\\n+/g, ' | ').slice(0, 220)
      }
      if (!confirmed) { confirmed = true; v2btn.click() }
    }
    if (document.querySelector('[data-testid="confirmation-card"]')) sawV1 = true
    const t = (document.body.innerText || '').replace(/\\n+/g, ' | ')
    if (t !== last) { last = t; settled = 0 } else { settled++ }
    const busy = !!document.querySelector('[data-testid*="stop"], [data-testid*="cancel"]')
    if (!busy && settled > 10) break
  }
  return { sawStep, sawV2, sawV1, confirmed, v2Payload, ms: Date.now() - t0, tail: last.slice(-260) }
`

console.log(`T-M7 工具测试集 · 设备端到端（${cases.length} 条，CDP ${PORT}）\n`)
await evalInPage(`location.hash = '#/assistant'
  for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 300))
    if (document.querySelector('input[placeholder], textarea[placeholder]')) break }
  return location.hash`)

const rows = []
for (const c of cases) {
  const n = await evalInPage(NEW_CONVERSATION, 30000)
  const r = await evalInPage(TURN(c.text), 150000)
  if (!r.ok || r.value === 'NO_INPUT') {
    console.log(`✗ ${c.id.padEnd(15)} ${c.text}\n    FAILED ${r.error ?? r.value}`)
    continue
  }
  const v = r.value
  const flags = `agent=${v.sawStep ? 'Y' : 'n'} v2卡=${v.sawV2 ? 'Y' : 'n'} v1卡=${v.sawV1 ? 'Y' : 'n'}`
  console.log(`· ${c.id.padEnd(15)} ${c.text}`)
  console.log(`    ${flags}  ${v.ms}ms  新会话前消息数=${n.value ?? '-'}`)
  if (v.v2Payload) console.log(`    卡面: ${v.v2Payload}`)
  console.log(`    收尾: ${v.tail}`)
  console.log()
  rows.push({ id: c.id, text: c.text, ...v })
}
ws.close()
console.log(`=== 汇总：${rows.length}/${cases.length} 条跑完 ===`)
console.log(`走了 agent 流式：${rows.filter(r => r.sawStep).length}  出 v2 卡：${rows.filter(r => r.sawV2).length}  出 v1 卡：${rows.filter(r => r.sawV1).length}`)