import type { ContextInfo } from '../types'
import styles from './StatusBar.module.css'

/** 底栏:模型名 + 上下文占用(§13-A4)+ 运行状态(工作目录在侧栏)。 */
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
  return (
    <div className={styles.root}>
      <span className={styles.dot} data-running={running || undefined} />
      <span className={styles.model}>{model || '—'}</span>
      <span className={styles.spacer} />
      {pct !== null && (
        <span
          className={styles.ctx}
          data-warn={warn || undefined}
          title={`${(context!.total_tokens / 1000).toFixed(1)}k / ${(context!.window / 1000).toFixed(0)}k tok`}
        >
          上下文 {pct}%
        </span>
      )}
      <span className={styles.status}>{running ? '工作中' : '空闲'}</span>
    </div>
  )
}
