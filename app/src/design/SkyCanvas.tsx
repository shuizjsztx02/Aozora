/**
 * SkyCanvas — 天空画布背景层。
 *
 * 约束：
 * - z-index ≤1 且 pointer-events: none
 * - 装饰不遮挡内容、不接收点击
 * - prefers-reduced-motion 时关闭动画
 * - T-M4-06b: 低性能模式关闭所有氛围装饰
 */

import type { JSX } from 'react'
import { useTheme, type ThemeName } from './theme'
import { useReducedMotion, useLowPerformance } from './a11y'

/** 各时段的装饰元素 */
function DawnDecor() {
  return (
    <>
      {/* 光束 */}
      <div className="absolute top-0 right-[20%] w-[2px] h-[40%] opacity-30"
        style={{ background: 'linear-gradient(180deg, rgba(255,200,150,0.6), transparent)' }} />
      <div className="absolute top-0 right-[40%] w-[2px] h-[30%] opacity-20"
        style={{ background: 'linear-gradient(180deg, rgba(255,180,120,0.5), transparent)' }} />
      {/* 云 */}
      <div className="absolute top-[15%] left-[10%] w-24 h-8 rounded-full opacity-40"
        style={{ background: 'rgba(255,255,255,0.6)', animation: 'float 20s ease-in-out infinite' }} />
      <div className="absolute top-[25%] right-[15%] w-16 h-6 rounded-full opacity-30"
        style={{ background: 'rgba(255,255,255,0.5)', animation: 'float 25s ease-in-out infinite reverse' }} />
    </>
  )
}

function DayDecor() {
  return (
    <>
      <div className="absolute top-[10%] left-[15%] w-28 h-10 rounded-full opacity-30"
        style={{ background: 'rgba(255,255,255,0.7)', animation: 'float 22s ease-in-out infinite' }} />
      <div className="absolute top-[20%] right-[20%] w-20 h-8 rounded-full opacity-25"
        style={{ background: 'rgba(255,255,255,0.6)', animation: 'float 28s ease-in-out infinite reverse' }} />
      <div className="absolute top-[8%] left-[50%] w-16 h-6 rounded-full opacity-20"
        style={{ background: 'rgba(255,255,255,0.5)', animation: 'float 18s ease-in-out infinite' }} />
    </>
  )
}

function DuskDecor() {
  return (
    <>
      {/* 暮光光束 */}
      <div className="absolute top-0 left-[30%] w-[3px] h-[50%] opacity-20"
        style={{ background: 'linear-gradient(180deg, rgba(230,120,80,0.5), transparent)' }} />
      <div className="absolute top-0 left-[60%] w-[2px] h-[35%] opacity-15"
        style={{ background: 'linear-gradient(180deg, rgba(200,100,60,0.4), transparent)' }} />
    </>
  )
}

function NightDecor() {
  const stars = Array.from({ length: 20 }, (_, i) => ({
    left: `${(i * 37 + 13) % 100}%`,
    top: `${(i * 23 + 7) % 60}%`,
    size: i % 3 === 0 ? 3 : 2,
    delay: i * 0.3,
  }))

  return (
    <>
      {stars.map((star, i) => (
        <div
          key={i}
          className="absolute rounded-full"
          style={{
            left: star.left,
            top: star.top,
            width: star.size,
            height: star.size,
            background: 'rgba(255,255,255,0.8)',
            animation: `twinkle ${2 + star.delay}s ease-in-out infinite`,
            animationDelay: `${star.delay}s`,
          }}
        />
      ))}
      {/* 彗尾 */}
      <div className="absolute top-[10%] right-[25%] w-1 h-1 rounded-full opacity-60"
        style={{
          background: 'white',
          boxShadow: '-20px 0 10px rgba(255,255,255,0.3), -40px 0 20px rgba(255,255,255,0.1)',
          animation: 'shootingStar 8s ease-in-out infinite',
        }} />
    </>
  )
}

const decorMap: Record<ThemeName, () => JSX.Element> = {
  dawn: DawnDecor,
  day: DayDecor,
  dusk: DuskDecor,
  night: NightDecor,
}

export function SkyCanvas() {
  const { theme } = useTheme()
  const reducedMotion = useReducedMotion()
  const lowPerf = useLowPerformance()

  // T-M4-06b: 低性能模式不渲染任何装饰
  // NOTE: useLowPerformance 已将 prefers-reduced-motion 作为低性能信号（a11y.ts:46），
  // 因此 reducedMotion=true 时 lowPerf 也为 true，会在此处提前 return。
  // 下方 reducedMotion CSS 注入分支在当前实现中不可达，保留作防御性冗余：
  // 若未来 lowPerf 的判定逻辑变化，此处仍能正确禁用动画。
  if (lowPerf) {
    return (
      <div className="sky-fixed" aria-hidden="true" data-testid="sky-canvas" data-low-perf="true" />
    )
  }

  const Decor = decorMap[theme]

  return (
    <div className="sky-fixed" aria-hidden="true" data-testid="sky-canvas">
      <Decor />
      <style>{`
        @keyframes float {
          0%, 100% { transform: translateX(0); }
          50% { transform: translateX(10px); }
        }
        @keyframes twinkle {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 1; }
        }
        @keyframes shootingStar {
          0% { transform: translate(0, 0); opacity: 0; }
          5% { opacity: 1; }
          15% { transform: translate(-100px, 50px); opacity: 0; }
          100% { opacity: 0; }
        }
        ${reducedMotion ? `
          .sky-fixed * { animation: none !important; }
        ` : ''}
      `}</style>
    </div>
  )
}
