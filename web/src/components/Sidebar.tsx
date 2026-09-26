import { useMemo, useState } from 'react'
import type { AgentState, SessionInfo } from '../types'
import styles from './Sidebar.module.css'

interface Props {
  sessions: SessionInfo[]
  state: AgentState | null
  workspaces: string[]
  activeWorkspace: string
  onNewSession: () => void
  onResume: (id: string) => void
  onOpenSettings: () => void
  onAddWorkspace: (path: string) => void
  onSwitchWorkspace: (path: string) => void
}

function baseName(p: string): string {
  const parts = p.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? p
}

/** 侧栏:工作区(项目)树 + 活动工作区下的会话清单(§10.5,ZCode 同款两级树)。 */
export default function Sidebar({
  sessions,
  state,
  workspaces,
  activeWorkspace,
  onNewSession,
  onResume,
  onOpenSettings,
  onAddWorkspace,
  onSwitchWorkspace,
}: Props) {
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [adding, setAdding] = useState(false)
  const [newPath, setNewPath] = useState('')

  const serverDir = state?.work_dir ?? ''
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

      <div className={styles.wsHeader}>
        <span className={styles.groupLabel}>工作区</span>
        <button
          className={styles.iconBtn}
          title="添加工作区"
          onClick={() => setAdding((v) => !v)}
        >
          +
        </button>
      </div>

      {adding && (
        <div className={styles.addWs}>
          <input
            className={styles.addWsInput}
            placeholder="输入目录路径,如 F:\Projects\demo"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && newPath.trim()) {
                onAddWorkspace(newPath.trim())
                setNewPath('')
                setAdding(false)
              }
            }}
            autoFocus
          />
          <button
            className={styles.addWsBtn}
            disabled={!newPath.trim()}
            onClick={() => {
              if (newPath.trim()) {
                onAddWorkspace(newPath.trim())
                setNewPath('')
                setAdding(false)
              }
            }}
          >
            添加
          </button>
        </div>
      )}

      <div className={styles.workspaceList}>
        {workspaces.map((ws) => {
          const active = ws === activeWorkspace
          const isServer = ws === serverDir
          return (
            <div key={ws}>
              <button
                className={`${styles.wsRow} ${active ? styles.wsActive : ''}`}
                onClick={() => onSwitchWorkspace(ws)}
                title={ws}
              >
                <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
                  <path
                    d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 2h4.5A1.5 1.5 0 0 1 14 6.5v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7z"
                    stroke="currentColor"
                    strokeWidth="1.2"
                  />
                </svg>
                <span className={styles.wsName}>{baseName(ws)}</span>
                {isServer && <span className={styles.wsLive}>●</span>}
              </button>
              {active && (
                <div className={styles.sessionList}>
                  {filtered.length === 0 && (
                    <div className={styles.empty}>
                      {isServer ? '暂无会话' : '该工作区未接入后端(仅浏览)'}
                    </div>
                  )}
                  {filtered.map((s) => (
                    <button
                      key={s.id}
                      className={`${styles.sessionRow} ${s.current ? styles.current : ''}`}
                      onClick={() => isServer && !s.current && onResume(s.id)}
                      title={s.id}
                    >
                      <span className={styles.sessionDot} data-running={s.running || undefined} />
                      <span className={styles.sessionId}>{s.id.slice(0, 18)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className={styles.foot}>
        <button className={styles.settingsBtn} onClick={onOpenSettings}>
          ⚙ 设置
        </button>
        <div className={styles.workDir}>{serverDir || ''}</div>
      </div>
    </div>
  )
}
