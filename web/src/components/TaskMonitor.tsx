import { useEffect, useState } from 'react'
import { api } from '../api'
import { fmtTokens } from '../format'
import styles from './TaskMonitor.module.css'

interface TaskInfo {
  id: string
  name: string
  status: string
  elapsed: number
  input_tokens: number
  output_tokens: number
  result_preview: string
}

function fmtElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`
  return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`
}

/** 顶栏后台任务监控:触发器按钮 + 下拉清单(打开时每 3s 轮询快照)。 */
export default function TaskMonitor() {
  const [open, setOpen] = useState(false)
  const [tasks, setTasks] = useState<TaskInfo[]>([])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const load = () => {
      api
        .tasks()
        .then((r) => {
          if (!cancelled) setTasks(r.tasks)
        })
        .catch(() => {})
    }
    load()
    const timer = setInterval(load, 3000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [open])

  const runningCount = tasks.filter((t) => t.status === 'running').length

  return (
    <div className={styles.root}>
      <button
        className={styles.trigger}
        data-on={open || undefined}
        title="后台任务"
        onClick={() => setOpen((v) => !v)}
      >
        <svg width={15} height={15} viewBox="0 0 16 16" fill="none" aria-hidden>
          <path
            d="M2 8a6 6 0 1 1 1.76 4.24M2 8V4.8M2 8h3.2"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinecap="round"
            transform="rotate(120 8 8)"
          />
        </svg>
        {runningCount > 0 && <span className={styles.badge}>{runningCount}</span>}
      </button>
      {open && (
        <div className={styles.panel}>
          <div className={styles.head}>
            <span>后台任务</span>
            <span className={styles.count}>{tasks.length}</span>
          </div>
          {tasks.length === 0 && <div className={styles.empty}>暂无后台任务</div>}
          {tasks.map((t) => (
            <div key={t.id} className={styles.row} data-status={t.status}>
              <div className={styles.rowHead}>
                <span className={styles.dot} />
                <span className={styles.name} title={t.id}>
                  {t.name}
                </span>
                <span className={styles.status}>{t.status}</span>
                <span className={styles.elapsed}>{fmtElapsed(t.elapsed)}</span>
              </div>
              {(t.input_tokens > 0 || t.output_tokens > 0) && (
                <div className={styles.tokens}>
                  tok {fmtTokens(t.input_tokens + t.output_tokens)}
                </div>
              )}
              {t.status !== 'running' && t.result_preview && (
                <div className={styles.preview}>{t.result_preview}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
