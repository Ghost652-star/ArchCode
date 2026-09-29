import { useRef, useState } from 'react'
import type { ContextInfo } from '../types'
import { fmtPercent, fmtTokens } from '../format'
import styles from './ContextMeter.module.css'

const RADIUS = 6
const CIRCUM = 2 * Math.PI * RADIUS

interface Segment {
  key: string
  label: string
  value: number
  color: string
}

/** 上下文占用圈 + 悬停标签卡(ZCode/DSH 形态:composer 旁小圈,hover 出分类明细)。 */
export default function ContextMeter({
  context,
  cacheHit,
}: {
  context: ContextInfo | null
  cacheHit: number | null
}) {
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const leaveTimer = useRef<number | null>(null)

  if (!context || context.total_tokens <= 0) return null
  const pct = Math.round(context.percent * 100)
  const warn = pct >= 80
  const bd = context.breakdown

  const segments: Segment[] = bd
    ? [
        { key: 'messages', label: '消息', value: bd.messages, color: 'var(--ac-brand-400)' },
        { key: 'tools_builtin', label: '系统工具', value: bd.tools_builtin, color: 'var(--ac-state-warn)' },
        { key: 'tools_mcp', label: 'MCP 工具', value: bd.tools_mcp, color: 'var(--ac-state-success)' },
        { key: 'skills', label: '技能', value: bd.skills, color: 'var(--ac-violet-400)' },
        { key: 'system', label: '系统提示词', value: bd.system, color: 'var(--ac-label-tertiary)' },
        { key: 'memory', label: '记忆', value: bd.memory, color: 'var(--ac-label-caption)' },
      ].filter((s) => s.value > 0)
    : []
  const segTotal = segments.reduce((acc, s) => acc + s.value, 0)

  const openCard = () => {
    if (leaveTimer.current) {
      window.clearTimeout(leaveTimer.current)
      leaveTimer.current = null
    }
    setOpen(true)
  }
  const scheduleClose = () => {
    if (pinned) return
    if (leaveTimer.current) window.clearTimeout(leaveTimer.current)
    leaveTimer.current = window.setTimeout(() => setOpen(false), 180)
  }

  return (
    <div className={styles.root} onMouseEnter={openCard} onMouseLeave={scheduleClose}>
      <button
        type="button"
        className={styles.chip}
        data-warn={warn || undefined}
        data-open={open || undefined}
        title="上下文占用"
        onClick={() => {
          if (pinned) {
            setPinned(false)
            setOpen(false)
          } else {
            setPinned(true)
            setOpen(true)
          }
        }}
      >
        <svg width={14} height={14} viewBox="0 0 16 16" aria-hidden>
          <circle cx="8" cy="8" r={RADIUS} fill="none" stroke="var(--ac-border-l2)" strokeWidth="2" />
          <circle
            cx="8"
            cy="8"
            r={RADIUS}
            fill="none"
            stroke={warn ? 'var(--ac-state-warn)' : 'var(--ac-brand-400)'}
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray={`${(pct / 100) * CIRCUM} ${CIRCUM}`}
            transform="rotate(-90 8 8)"
          />
        </svg>
        <span className={styles.chipText}>{pct}%</span>
      </button>
      {open && (
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <span>上下文容量</span>
            <span className={styles.cardHeadNum}>
              {fmtTokens(context.total_tokens)} / {fmtTokens(context.window)}（{pct}%）
            </span>
          </div>
          <div className={styles.bar}>
            <div
              className={styles.barFill}
              data-warn={warn || undefined}
              style={{ width: `${Math.min(context.percent * 100, 100)}%` }}
            />
          </div>
          {segments.map((s) => (
            <div key={s.key} className={styles.row}>
              <span className={styles.dot} style={{ background: s.color }} />
              <span className={styles.label}>{s.label}</span>
              <span className={styles.value}>
                {segTotal > 0 ? `${((s.value / segTotal) * 100).toFixed(1)}%` : '—'}
                <span className={styles.valueAbs}>{fmtTokens(s.value)}</span>
              </span>
            </div>
          ))}
          <div className={styles.foot}>
            <span>平均缓存命中率</span>
            <span className={styles.footNum}>{fmtPercent(cacheHit)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
