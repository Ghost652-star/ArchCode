import type { ContextInfo } from '../types'
import { fmtTokens } from '../format'
import styles from './StatusBar.module.css'

/** 底栏:模型名 + 上下文占用(分段估算条)+ 运行状态(工作目录在侧栏)。 */
export default function StatusBar({
  model,
  running,
  context,
}: {
  model: string
  running: boolean
  context: ContextInfo | null
}) {
  const pct = context ? Math.round(context.percent * 100) : null
  const warn = pct !== null && pct >= 80
  const bd = context?.breakdown
  const tooltip = context
    ? `${(context.total_tokens / 1000).toFixed(1)}k / ${(context.window / 1000).toFixed(0)}k tok` +
      (bd
        ? ` · 系统 ${fmtTokens(bd.system)} · 工具 ${fmtTokens(bd.tools)} · 消息 ${fmtTokens(bd.messages)}`
        : '')
    : ''
  const total = bd ? bd.system + bd.tools + bd.messages : 0
  const seg = (n: number) => (total > 0 ? `${(n / total) * 100}%` : '0%')
  return (
    <div className={styles.root}>
      <span className={styles.dot} data-running={running || undefined} />
      <span className={styles.model}>{model || '—'}</span>
      <span className={styles.spacer} />
      {pct !== null && (
        <span className={styles.ctx} data-warn={warn || undefined} title={tooltip}>
          上下文 {pct}%
          {bd && (
            <span className={styles.meter} aria-hidden>
              <span className={styles.segSystem} style={{ width: seg(bd.system) }} />
              <span className={styles.segTools} style={{ width: seg(bd.tools) }} />
              <span className={styles.segMessages} style={{ width: seg(bd.messages) }} />
            </span>
          )}
        </span>
      )}
      <span className={styles.status}>{running ? '工作中' : '空闲'}</span>
    </div>
  )
}
