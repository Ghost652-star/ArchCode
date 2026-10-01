import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { SessionInfo } from '../types'
import styles from './Sidebar.module.css'

interface Props {
  /** 全部工作区的会话清单(各组同时展示,键 = 工作区路径)。 */
  sessionsByWs: Record<string, SessionInfo[]>
  openWorkspace: string
  openSessionId: string | null
  workspaces: string[]
  activeWorkspace: string
  collapsed: boolean
  onToggleCollapse: () => void
  onNewSession: () => void
  /** 打开会话:跨项目时先切换工作区再恢复(App 内编排)。 */
  onOpenSession: (ws: string, id: string) => void | Promise<void>
  onOpenSettings: () => void
  onAddWorkspace: (path: string) => void
  onSwitchWorkspace: (path: string) => void
  /** 会话变更后的刷新:参数为被删除的会话 id(删除之外的操作传 null)。 */
  onSessionsChanged: (deletedId: string | null) => void
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

/** 侧栏:工作区分组树,每组同时展示各自会话,可折叠;点击跨项目会话自动切换(§10.5/§10.9-4)。 */
export default function Sidebar({
  sessionsByWs,
  openWorkspace,
  openSessionId,
  workspaces,
  activeWorkspace,
  collapsed,
  onToggleCollapse,
  onNewSession,
  onOpenSession,
  onOpenSettings,
  onAddWorkspace,
  onSwitchWorkspace,
  onSessionsChanged,
}: Props) {
  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [newPath, setNewPath] = useState('')
  const [picking, setPicking] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [contentResults, setContentResults] = useState<
    Array<{ id: string; title: string; excerpt: string }>
  >([])
  // 折叠的分组(持久化;默认全展开)
  const [folded, setFolded] = useState<string[]>(() => {
    try {
      const raw = JSON.parse(localStorage.getItem('ac-ws-folded') ?? '[]')
      return Array.isArray(raw) ? raw : []
    } catch {
      return []
    }
  })

  const toggleFold = (ws: string) => {
    setFolded((prev) => {
      const next = prev.includes(ws) ? prev.filter((p) => p !== ws) : [...prev, ws]
      localStorage.setItem('ac-ws-folded', JSON.stringify(next))
      return next
    })
  }

  const groupSessions = useCallback(
    (ws: string) =>
      (sessionsByWs[ws] ?? []).filter(
        (s) =>
          !query ||
          s.id.toLowerCase().includes(query.toLowerCase()) ||
          (s.title ?? '').toLowerCase().includes(query.toLowerCase()),
      ),
    [sessionsByWs, query],
  )

  // 内容搜索(≥2 字符触发,防抖 300ms):服务端扫会话 JSONL,返回首次命中摘要
  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setContentResults([])
      return
    }
    const timer = setTimeout(() => {
      api
        .sessionsSearch(activeWorkspace, q)
        .then((r) => setContentResults(r.results))
        .catch(() => setContentResults([]))
    }, 300)
    return () => clearTimeout(timer)
  }, [query])

  const commitRename = useCallback(
    async (id: string) => {
      const value = renameValue.trim()
      setRenamingId(null)
      if (!value) return
      try {
        await api.renameSession(id, activeWorkspace, value)
        onSessionsChanged(null)
      } catch {
        /* 改名失败静默:列表下次刷新会显示原名 */
      }
    },
    [renameValue, activeWorkspace, onSessionsChanged],
  )

  const removeSession = useCallback(
    async (s: SessionInfo) => {
      if (!window.confirm(`删除会话 "${s.title || s.id.slice(0, 18)}"?`)) return
      try {
        await api.deleteSession(s.id, activeWorkspace)
        onSessionsChanged(s.id)
      } catch {
        /* 运行中删除被服务端 409 拒绝,静默 */
      }
    },
    [activeWorkspace, onSessionsChanged],
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
        <span className={styles.wsHeaderActions}>
          <button
            className={styles.iconBtn}
            title="搜索会话"
            data-on={searchOpen || undefined}
            onClick={() => {
              setSearchOpen((v) => !v)
              setQuery('')
            }}
          >
            <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
              <circle cx="7" cy="7" r="4.2" stroke="currentColor" strokeWidth="1.3" />
              <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
          </button>
          <button
            className={styles.iconBtn}
            title="添加工作区"
            onClick={() => setAdding((v) => !v)}
          >
            +
          </button>
        </span>
      </div>

      {searchOpen && (
        <input
          className={styles.search}
          placeholder="搜索会话(标题 / ID / 内容)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              setSearchOpen(false)
              setQuery('')
              setContentResults([])
            }
          }}
          autoFocus
        />
      )}

      {adding && (
        <div className={styles.addWs}>
          <button
            type="button"
            className={styles.pickBtn}
            disabled={picking}
            onClick={async () => {
              setPicking(true)
              try {
                const r = await api.pickDirectory()
                if (r.path) {
                  onAddWorkspace(r.path)
                  setAdding(false)
                }
              } catch (e) {
                alert(e instanceof Error ? e.message : String(e))
              } finally {
                setPicking(false)
              }
            }}
          >
            {picking ? '请在弹出的窗口中选择…' : '📂 选择文件夹…'}
          </button>
          <div className={styles.addWsRow}>
            <input
              className={styles.addWsInput}
              placeholder="或手动输入路径,如 F:\Projects\demo"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newPath.trim()) {
                  onAddWorkspace(newPath.trim())
                  setNewPath('')
                  setAdding(false)
                }
              }}
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
        </div>
      )}

      <div className={styles.workspaceList}>
        {workspaces.map((ws) => {
          const active = ws === activeWorkspace
          const isLive = ws === openWorkspace
          const group = groupSessions(ws)
          const foldedGroup = folded.includes(ws)
          return (
            <div key={ws}>
              <div
                className={`${styles.wsRow} ${active ? styles.wsActive : ''}`}
                onClick={() => onSwitchWorkspace(ws)}
                title={ws}
              >
                <button
                  type="button"
                  className={styles.wsFold}
                  aria-label={foldedGroup ? '展开会话列表' : '折叠会话列表'}
                  onClick={(e) => {
                    e.stopPropagation()
                    toggleFold(ws)
                  }}
                >
                  <svg
                    width={10}
                    height={10}
                    viewBox="0 0 16 16"
                    fill="none"
                    aria-hidden
                    data-open={!foldedGroup || undefined}
                  >
                    <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                  </svg>
                </button>
                <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
                  <path
                    d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 2h4.5A1.5 1.5 0 0 1 14 6.5v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7z"
                    stroke="currentColor"
                    strokeWidth="1.2"
                  />
                </svg>
                <button type="button" className={styles.wsNameBtn} onClick={() => onSwitchWorkspace(ws)}>
                  <span className={styles.wsName}>{baseName(ws)}</span>
                </button>
                {isLive && <span className={styles.wsLive}>●</span>}
              </div>
              {!foldedGroup && (
                <div className={styles.sessionList}>
                  {group.length === 0 && (
                    <div className={styles.empty}>暂无会话</div>
                  )}
                  {group.map((s) => {
                    const renaming = renamingId === s.id
                    return (
                      <div
                        key={s.id}
                        className={styles.sessionRow}
                        data-current={openSessionId === s.id || undefined}
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
                              onClick={() => onOpenSession(ws, s.id)}
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
                            {(
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
        {contentResults.length > 0 && (
          <div className={styles.contentResults}>
            <div className={styles.groupLabel}>内容匹配(当前工作区)</div>
            {contentResults.map((r) => (
              <button
                key={r.id}
                className={styles.contentRow}
                onClick={() => onOpenSession(openWorkspace, r.id)}
                title={r.excerpt}
              >
                <span className={styles.sessionTitle}>
                  {r.title || r.id.slice(0, 18)}
                </span>
                <span className={styles.contentExcerpt}>{r.excerpt}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className={styles.foot}>
        <button className={styles.settingsBtn} onClick={onOpenSettings}>
          ⚙ 设置
        </button>
      </div>
    </div>
  )
}
