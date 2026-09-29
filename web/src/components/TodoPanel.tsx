import { useState } from 'react'
import type { TodoItem } from '../types'
import styles from './TodoPanel.module.css'

/** 输入区上方的任务清单卡:按状态计数摘要,可折叠,逐项状态点。 */
export default function TodoPanel({ todos }: { todos: TodoItem[] }) {
  const [expanded, setExpanded] = useState(true)
  if (todos.length === 0) return null
  const done = todos.filter((t) => t.status === 'completed').length
  const inProgress = todos.filter((t) => t.status === 'in_progress').length
  return (
    <div className={styles.root}>
      <button type="button" className={styles.head} onClick={() => setExpanded((v) => !v)}>
        <span className={styles.title}>任务清单</span>
        <span className={styles.summary}>
          {[
            inProgress > 0 ? `${inProgress} 进行中` : null,
            `${done}/${todos.length} 完成`,
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
        <svg
          className={styles.chevron}
          width={11}
          height={11}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
          data-open={expanded || undefined}
        >
          <path d="M3 6l5 5 5-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      {expanded && (
        <ul className={styles.list}>
          {todos.map((t, i) => (
            <li key={i} className={styles.item} data-status={t.status}>
              <span className={styles.dot} aria-hidden>
                {t.status === 'completed' ? '✓' : t.status === 'in_progress' ? '●' : '○'}
              </span>
              <span className={styles.text}>{t.content}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
