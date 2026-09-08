 # Aozora 四时天空设计令牌
 
 **状态：** 已定稿（V5.2） | **视觉唯一事实源：** 原 `docs/uiux-redesign-proposal.html`
 
 ## 设计原则
 - 素贝 MONO 骨架（TikTok 七规则）+ 四时天空画布
 - 装饰层（云/星/光束）z-index ≤1 且 pointer-events:none，永不置于正文之上
 - 玻璃卡面正文对比度 ≥4.5:1，四时段逐个验证
 
 ## 四时主题 Token
 
 根节点挂 `.t-dawn / .t-day / .t-dusk / .t-night` 类切换 CSS 变量。
 
 | Token | 晨 dawn (05-10) | 昼 day (10-17) | 暮 dusk (17-20) | 夜 night (20-05) |
 |---|---|---|---|---|
 | --ink 主文字 | #1E2733 | #122031 | #241C36 | #EFF1F8 |
 | --ink2 次文字 | #46536B | #3C5069 | #4E4066 | #B6BCCF |
 | --ink3 辅助文字 | #526078 * | #4A6380 * | #554575 * | #7A83A3 |
 | --accent 强调 | #D96F5A | #1D6CB0 | #D95F3B | #E9C56A |
 | --glass 卡面 | rgba(255,253,250,.6) | rgba(255,255,255,.52) | rgba(255,249,242,.68) * | rgba(14,19,40,.55) |
 | --income 收入色 | #1E9E6A | #1E9E6A | #1E9E6A | #4CC38A |
 | --warn 警告色 | #A8730F | #A8730F | #E9C56A | #E9C56A |

 *T-M4-06a 调整：ink3 加深以满足 WCAG AA ≥4.5:1；dusk glass 不透明度提高以减少渐变背景渗透。

 ## 扩展 Token（应用特定，补充设计令牌表）

 这些 token 由前端 `index.css` 定义，不覆盖核心业务语义，但需文档化以便后续维护。

 | Token | 晨 dawn | 昼 day | 暮 dusk | 夜 night | 用途 |
 |---|---|---|---|---|---|
 | --glass-border 卡面边框 | `rgba(255,255,255,.3)` | `rgba(255,255,255,.3)` | `rgba(255,255,255,.3)` | `rgba(200,150,100,.25)` | 玻璃卡片、分隔线、底部导航边框 |
 | --income 收入色 | `#1E9E6A` | `#1E9E6A` | `#1E9E6A` | `#4CC38A` | 收入金额文字颜色（与主表一致） |
 | --expense 支出色 | `#D95F3B` | `#D95F3B` | `#D95F3B` | `#E8756A` | 支出金额文字颜色（night 时段替换低对比色） |
 | --sky-from 天空顶色 | `#87CEEB` | `#87CEEB` | `#FFB88C` | `#E8756A` | 天空画布渐变起始（SkyCanvas 装饰层） |
 | --sky-to 天空底色 | `#E0F4FF` | `#E0F4FF` | `#FFE4D6` | `#2D1B4E` | 天空画布渐变结束（SkyCanvas 装饰层） |
 | --nav-bg 底栏背景 | `rgba(255,255,255,.7)` | `rgba(255,255,255,.52)` | `rgba(255,250,245,.75)` | `rgba(60,40,70,.65)` | 底部导航栏背景（实际使用 index.css 中精确值） |
 
 ## 时段映射
 | 时段 | 时间范围 | 装饰层 |
 |---|---|---|
 | 晨 dawn | 05:00-09:59 | 云 + 光束 |
 | 昼 day | 10:00-16:59 | 云 |
 | 暮 dusk | 17:00-19:59 | 暮光光束 |
 | 夜 night | 20:00-04:59 | 星 + 彗尾 |
 
 `themeMode` 支持 auto / 跟随系统深色 / 手动锁定（夜间即 night 主题）。
 
 ## 关键 CSS 类
 - `glass-card`：玻璃材质卡片（`backdrop-filter: blur(20px)`）
 - `sheet-glass`：底部弹层（`border-radius: 16px 16px 0 0`）
 - `nav-glass`：底栏导航
 - `row-line`：流水行分隔线
 - `cat-cell`：分类网格格
 - `key-btn`：数字键盘按钮
 - `sky-fixed`：天空画布容器
 
 ## 图表色板
 Recharts 环图/趋势图在各时段 token 上验证可读性后固化，图例/标签在所有时段可读。
 
 ## 排版
 - 字体：`-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif`
 - 日期数字：`serif-date` 类（`Noto Serif SC`），letter-spacing: .2em
 - 金额数字：`money-num` 类（`font-variant-numeric: tabular-nums`）
 
 ## 动效原则
 - Framer Motion 仅用于微反馈，克制使用
 - 页面切换：opacity 0.18s easeOut
 - AI 弹层：spring 380/34 阻尼
 - 数字滚动遵循提案样机
 - 低端机（Android 8/2GB）不应有明显掉帧
