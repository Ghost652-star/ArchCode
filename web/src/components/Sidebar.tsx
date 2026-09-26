import { useMemo, useState } from 'react'
import type { AgentState, SessionInfo } from '../types'
import styles from './Sidebar.module.css'

interface Props {
  sessions: SessionInfo[]
  state: AgentState | null
  onNewSession: () => void
  onResume: (id: string) => void
  onOpenSettings: () => void
}

export default function Sidebar({ sessions, state, onNewSession, onResume, onOpenSettings }: Props) {
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState(false)

  const filtered = useMemo(
    () =>
      sessions.filter(
        (s) => !query || s.id.toLowerCase().includes(query.toLowerCase()),
      ),
    [sessions, query],
  )

  if (collapsed) {
    return (
      <div className={styles.collapsed}>
        <button className={styles.iconBtn} onClick={() => setCollapsed(false)} title="展开">
          »
        </button>
      </div>
    )
  }

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <span className={styles.brand}>ArchCode</span>
        <button className={styles.iconBtn} onClick={() => setCollapsed(true)} title="折叠">
          «
        </button>
      </div>
      <button className={styles.newBtn} onClick={onNewSession}>
        + 新对话
      </button>
      <input
        className={styles.search}
        placeholder="搜索会话…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className={styles.list}>
        <div className={styles.groupLabel}>当前项目</div>
        {filtered.length === 0 && <div className={styles.empty}>暂无会话</div>}
        {filtered.map((s) => (
          <button
            key={s.id}
            className={`${styles.sessionRow} ${s.current ? styles.current : ''}`}
            onClick={() => !s.current && onResume(s.id)}
            title={s.id}
          >
            <span className={styles.sessionDot} data-running={s.running || undefined} />
            <span className={styles.sessionId}>{s.id.slice(0, 18)}</span>
          </button>
        ))}
      </div>
      <div className={styles.foot}>
        <button className={styles.settingsBtn} onClick={onOpenSettings}>
          ⚙ 设置
        </button>
        <div className={styles.workDir}>{state?.work_dir ?? ''}</div>
      </div>
    </div>
  )
}
