import { useCallback, useMemo, useState } from 'react'
import { api } from '../api'
import type { AgentState, SessionInfo } from '../types'
import styles from './Sidebar.module.css'

interface Props {
  sessions: SessionInfo[]
  state: AgentState | null
  workspaces: string[]
  activeWorkspace: string
  collapsed: boolean
  onToggleCollapse: () => void
  onNewSession: () => void
  onResume: (id: string) => void
  onOpenSettings: () => void
  onAddWorkspace: (path: string) => void
  onSwitchWorkspace: (path: string) => void
  /** 会话变更后的刷新:改名传 false;删除当前会话传 true(清空对话区)。 */
  onSessionsChanged: (currentDeleted: boolean) => void
}

function baseName(p: string): string {
  const parts = p.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? p
}

/** last_active_ms → 相对时间(DSH 侧栏同款:20小时 / 2天 / 日期)。 */
function relTime(ms?: number): string {
  if (!ms) return ''
  const diff = Date.now() - ms
  const minute = 60_000
  const hour = 3_600_000
  const day = 86_400_000
  if (diff < hour) return `${Math.max(1, Math.floor(diff / minute))}分钟`
  if (diff < day) return `${Math.floor(diff / hour)}小时`
  if (diff < 30 * day) return `${Math.floor(diff / day)}天`
  return new Date(ms).toLocaleDateString()
}

/** 侧栏:工作区(项目)树 + 活动工作区下的会话清单(§10.5/§10.9-4)。 */
export default function Sidebar({
  sessions,
  state,
  workspaces,
  activeWorkspace,
  collapsed,
  onToggleCollapse,
  onNewSession,
  onResume,
  onOpenSettings,
  onAddWorkspace,
  onSwitchWorkspace,
  onSessionsChanged,
}: Props) {
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [newPath, setNewPath] = useState('')
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  const serverDir = state?.work_dir ?? ''
  const filtered = useMemo(
    () =>
      sessions.filter(
        (s) =>
          !query ||
          s.id.toLowerCase().includes(query.toLowerCase()) ||
          (s.title ?? '').toLowerCase().includes(query.toLowerCase()),
      ),
    [sessions, query],
  )

  const commitRename = useCallback(
    async (id: string) => {
      const value = renameValue.trim()
      setRenamingId(null)
      if (!value) return
      try {
        await api.renameSession(id, value)
        onSessionsChanged(false)
      } catch {
        /* 改名失败静默:列表下次刷新会显示原名 */
      }
    },
    [renameValue, onSessionsChanged],
  )

  const removeSession = useCallback(
    async (s: SessionInfo) => {
      if (!window.confirm(`删除会话 "${s.title || s.id.slice(0, 18)}"?`)) return
      try {
        await api.deleteSession(s.id)
        onSessionsChanged(Boolean(s.current))
      } catch {
        /* 运行中删除被服务端 409 拒绝,静默 */
      }
    },
    [onSessionsChanged],
  )

  // 折叠 = 56px 图标栏(DSH 同款:展开入口常驻,不消失)
  if (collapsed) {
    return (
      <div className={styles.collapsed}>
        <button className={styles.iconBtn} onClick={onToggleCollapse} title="展开侧栏">
          »
        </button>
        <span className={styles.railSpring} />
        <button className={styles.iconBtn} onClick={onNewSession} title="新对话">
          +
        </button>
        <button className={styles.iconBtn} onClick={onOpenSettings} title="设置">
          ⚙
        </button>
      </div>
    )
  }

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <span className={styles.brand}>ArchCode</span>
        <button className={styles.iconBtn} onClick={onToggleCollapse} title="折叠侧栏">
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
                  {filtered.map((s) => {
                    const renaming = renamingId === s.id
                    return (
                      <div
                        key={s.id}
                        className={styles.sessionRow}
                        data-current={s.current || undefined}
                      >
                        {renaming ? (
                          <input
                            className={styles.renameInput}
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') commitRename(s.id)
                              if (e.key === 'Escape') setRenamingId(null)
                            }}
                            onBlur={() => commitRename(s.id)}
                            autoFocus
                          />
                        ) : (
                          <>
                            <button
                              className={styles.sessionMain}
                              onClick={() => isServer && !s.current && onResume(s.id)}
                              title={s.id}
                            >
                              <span
                                className={styles.sessionDot}
                                data-running={s.running || undefined}
                              />
                              <span className={styles.sessionTitle}>
                                {s.title || s.id.slice(0, 18)}
                              </span>
                            </button>
                            {isServer && (
                              <span className={styles.rowActions}>
                                <button
                                  className={styles.rowAction}
                                  title="重命名会话"
                                  onClick={() => {
                                    setRenamingId(s.id)
                                    setRenameValue(s.title ?? '')
                                  }}
                                >
                                  ✎
                                </button>
                                <button
                                  className={styles.rowAction}
                                  title={s.running ? '运行中,无法删除' : '删除会话'}
                                  disabled={s.running}
                                  onClick={() => removeSession(s)}
                                >
                                  ✕
                                </button>
                              </span>
                            )}
                            <span className={styles.sessionTime}>
                              {relTime(s.last_active_ms)}
                            </span>
                          </>
                        )}
                      </div>
                    )
                  })}
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
