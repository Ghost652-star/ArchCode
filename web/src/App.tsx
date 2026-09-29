import { useCallback, useEffect, useRef, useState } from 'react'
import { api, streamChat } from './api'
import type { AgentState, ContextInfo, Item, PermissionState, SessionInfo, TodoItem, UsageTotal, WireEvent } from './types'
import Sidebar from './components/Sidebar'
import Composer from './components/Composer'
import PermissionDialog from './components/PermissionDialog'
import StatusBar from './components/StatusBar'
import SettingsPanel from './components/SettingsPanel'
import FilePanel from './components/FilePanel'
import TaskMonitor from './components/TaskMonitor'
import TodoPanel from './components/TodoPanel'
import { ChatItems } from './components/ChatItems'
import styles from './App.module.css'

const WS_KEY = 'ac-workspaces'
const ACTIVE_WS_KEY = 'ac-active-workspace'

// ── 三栏几何常量(设计 §11.2,DSH 同源,数值可微调)──────────────────
const SIDEBAR_MIN = 264
const SIDEBAR_MAX = 420
const SIDEBAR_DEFAULT = 280
const SIDEBAR_COLLAPSED = 56
const FILES_MIN = 300
const FILES_MAX_RATIO = 0.7
const FILES_DEFAULT_RATIO = 0.45

function loadNum(key: string, fallback: number): number {
  const raw = Number(localStorage.getItem(key))
  return Number.isFinite(raw) && raw > 0 ? raw : fallback
}
function loadBool(key: string, fallback: boolean): boolean {
  const raw = localStorage.getItem(key)
  return raw === null ? fallback : raw === '1'
}

function clampWidth(px: number, min: number, max: number): number {
  return Math.min(Math.round(max), Math.max(min, Math.round(px)))
}

/** /api/history → 渲染条目(工作区切换与 resume 共用):工具行按 tool_use_id 配对回填。 */
function historyToItems(history: Array<Record<string, unknown>>): Item[] {
  const restored: Item[] = []
  for (const m of history) {
    const role = m['role']
    const content = String(m['content'] ?? '')
    const ts = Number(m['created_at'] ?? 0) || undefined
    const uses = Array.isArray(m['tool_uses']) ? m['tool_uses'] : []
    for (const u of uses) {
      restored.push({
        kind: 'tool',
        toolId: String(u['tool_use_id'] ?? ''),
        toolName: String(u['tool_name'] ?? ''),
        args: (u['arguments'] as Record<string, unknown>) ?? {},
        output: '',
        isError: false,
        running: false,
      })
    }
    const results = Array.isArray(m['tool_results']) ? m['tool_results'] : []
    for (const r of results) {
      const tool = [...restored]
        .reverse()
        .find((i) => i.kind === 'tool' && i.toolId === String(r['tool_use_id'] ?? ''))
      if (tool && tool.kind === 'tool') {
        tool.output = String(r['content'] ?? '')
        tool.isError = Boolean(r['is_error'])
      }
    }
    if (role === 'user' && content) restored.push({ kind: 'user', text: content, ts })
    if (role === 'assistant' && content)
      restored.push({ kind: 'assistant', text: content, running: false })
  }
  return restored
}

/** 流结束后把仍在转动的条目落定(仅这三类携带 running 标志)。 */
function settleRunning(items: Item[]): void {
  for (const item of items)
    if (item.kind === 'reasoning' || item.kind === 'tool' || item.kind === 'assistant')
      item.running = false
}

function loadWorkspaces(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(WS_KEY) ?? '[]')
    return Array.isArray(raw) ? raw.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

export default function App() {
  const [items, setItems] = useState<Item[]>([])
  const [running, setRunning] = useState(false)
  const [permission, setPermission] = useState<PermissionState | null>(null)
  const [usage, setUsage] = useState<UsageTotal | null>(null)
  const [todos, setTodos] = useState<TodoItem[]>([])
  const [state, setState] = useState<AgentState | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [providers, setProviders] = useState<
    Array<{ name: string; model: string; protocol: string }>
  >([])
  const [context, setContext] = useState<ContextInfo | null>(null)
  const [workspaces, setWorkspaces] = useState<string[]>(loadWorkspaces)
  const [activeWorkspace, setActiveWorkspace] = useState(
    () => localStorage.getItem(ACTIVE_WS_KEY) ?? '',
  )
  // ── 三栏布局状态(设计 §11.5:宽度偏好进 localStorage)──────────
  const [sidebarW, setSidebarW] = useState(() =>
    clampWidth(loadNum('ac-sidebar-w', SIDEBAR_DEFAULT), SIDEBAR_MIN, SIDEBAR_MAX),
  )
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
    loadBool('ac-sidebar-collapsed', false),
  )
  const [filesOpen, setFilesOpen] = useState(() => loadBool('ac-files-open', true))
  const [filesW, setFilesW] = useState(() =>
    clampWidth(
      loadNum('ac-files-w', Math.round(window.innerWidth * FILES_DEFAULT_RATIO)),
      FILES_MIN,
      Math.max(FILES_MIN, window.innerWidth * FILES_MAX_RATIO),
    ),
  )
  const [dragging, setDragging] = useState(false)
  const [animating, setAnimating] = useState(false)
  const itemsRef = useRef<Item[]>([])
  const rafRef = useRef(0)
  const runStartRef = useRef(0)
  // 智能滚底(§13-A3):距底 >80px 视为"脱离底部",不再跟随
  const scrollRef = useRef<HTMLDivElement>(null)
  const detachedRef = useRef(false)
  const [detached, setDetached] = useState(false)

  const serverDir = state?.work_dir ?? ''
  /** 活动工作区是否就是服务端绑定的工作目录(仅此可用 composer/发消息)。 */
  const isHome = Boolean(
    serverDir &&
      activeWorkspace &&
      activeWorkspace.replace(/\\/g, '/').toLowerCase() ===
        serverDir.replace(/\\/g, '/').toLowerCase(),
  )

  /** itemsRef 变更后 rAF 批量刷新(高频 delta 不逐条 setState)。 */
  const render = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0
      setItems([...itemsRef.current])
    })
  }, [])

  const refreshMeta = useCallback(async () => {
    try {
      setState(await api.state())
      setSessions(await api.sessionsByWorkspace(activeWorkspace || undefined))
      const m = await api.model()
      setProviders(m.providers)
      api.context().then(setContext).catch(() => {})
      api.usage().then(setUsage).catch(() => {})
      api.todo().then((r) => setTodos(r.todos)).catch(() => {})
    } catch {
      /* 服务端未就绪时静默 */
    }
  }, [activeWorkspace])

  useEffect(() => {
    refreshMeta()
  }, [refreshMeta])

  // 服务端目录加载后,把 activeWorkspace 默认为它并并入工作区清单
  useEffect(() => {
    if (!serverDir) return
    setWorkspaces((prev) => {
      const next = prev.includes(serverDir) ? prev : [...prev, serverDir]
      if (next !== prev) localStorage.setItem(WS_KEY, JSON.stringify(next))
      return next
    })
    setActiveWorkspace((prev) => {
      if (prev) return prev
      localStorage.setItem(ACTIVE_WS_KEY, serverDir)
      return serverDir
    })
  }, [serverDir])

  // 切换工作区:刷新该工作区的会话清单;恢复其历史(仅 home 可恢复)
  useEffect(() => {
    if (!activeWorkspace) return
    localStorage.setItem(ACTIVE_WS_KEY, activeWorkspace)
    let cancelled = false
    ;(async () => {
      try {
        const list = await api.sessionsByWorkspace(activeWorkspace || undefined)
        if (cancelled) return
        setSessions(list)
        if (isHome) {
          const history = await api.history()
          if (cancelled) return
          const restored = historyToItems(history)
          itemsRef.current = restored
          setItems([...restored])
        } else {
          itemsRef.current = []
          setItems([])
        }
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [activeWorkspace, isHome])

  // 新条目自动滚底(§13-A3:near-bottom 才跟随,不拽上翻的用户)
  useEffect(() => {
    const el = scrollRef.current
    if (el && !detachedRef.current) el.scrollTop = el.scrollHeight
  }, [items])

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const d = el.scrollHeight - el.scrollTop - el.clientHeight > 80
    if (d !== detachedRef.current) {
      detachedRef.current = d
      setDetached(d)
    }
  }, [])

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    detachedRef.current = false
    setDetached(false)
  }, [])

  const handleEvent = useCallback(
    (event: WireEvent) => {
      const list = itemsRef.current
      const last = list[list.length - 1]
      switch (event.type) {
        case 'thinking': {
          const text = String(event['text'] ?? '')
          if (last && last.kind === 'reasoning' && last.running) {
            last.text += text
          } else {
            list.push({ kind: 'reasoning', text, running: true })
          }
          break
        }
        case 'text': {
          const text = String(event['text'] ?? '')
          if (last && last.kind === 'assistant' && last.running) {
            last.text += text
          } else {
            list.push({
              kind: 'assistant',
              text,
              running: true,
              ts: Number(event['ts'] ?? 0) || undefined,
            })
          }
          break
        }
        case 'tool_use': {
          list.push({
            kind: 'tool',
            toolId: String(event['tool_id']),
            toolName: String(event['tool_name']),
            args: (event['arguments'] as Record<string, unknown>) ?? {},
            output: '',
            isError: false,
            running: true,
          })
          break
        }
        case 'tool_result': {
          const toolId = String(event['tool_id'])
          const tool = [...list].reverse().find((i) => i.kind === 'tool' && i.toolId === toolId)
          if (tool && tool.kind === 'tool') {
            tool.output = String(event['output'] ?? '')
            tool.isError = Boolean(event['is_error'])
            tool.running = false
            tool.elapsed = Number(event['elapsed'] ?? 0)
          }
          // TodoWrite 的结果事件附带清单快照,驱动输入区上方面板
          if (Array.isArray(event['todos'])) setTodos(event['todos'] as TodoItem[])
          break
        }
        case 'permission_request': {
          setPermission({
            requestId: String(event['request_id']),
            toolName: String(event['tool_name'] ?? ''),
            reason: String(event['reason'] ?? ''),
            question: (event['question'] as string) ?? null,
            options: (event['options'] as string[]) ?? null,
            multiSelect: Boolean(event['multi_select']),
          })
          break
        }
        case 'error': {
          list.push({ kind: 'error', message: String(event['message'] ?? '') })
          break
        }
        case 'notice': {
          list.push({ kind: 'notice', text: String(event['text'] ?? '') })
          break
        }
        case 'retry': {
          const wait = Number(event['wait'] ?? 0)
          list.push({
            kind: 'notice',
            text: `请求失败，${wait > 0 ? `${wait}s 后` : ''}自动重试：${String(event['reason'] ?? '')}`,
          })
          break
        }
        case 'compact_started': {
          list.push({
            kind: 'compact',
            state: 'running',
            mode: String(event['mode'] ?? ''),
            totalChars: 0,
          })
          break
        }
        case 'compact_progress': {
          for (let i = list.length - 1; i >= 0; i--) {
            const it = list[i]
            if (it.kind === 'compact' && it.state === 'running') {
              it.totalChars = Number(event['total_chars'] ?? 0)
              break
            }
          }
          break
        }
        case 'compact_finished': {
          for (let i = list.length - 1; i >= 0; i--) {
            const it = list[i]
            if (it.kind === 'compact' && it.state === 'running') {
              const success = Boolean(event['success'])
              it.state = success ? 'done' : 'failed'
              it.dropped = Number(event['dropped'] ?? 0)
              it.summaryPreview = String(event['summary_preview'] ?? '')
              it.error = String(event['error'] ?? '')
              break
            }
          }
          // 压缩会即时改写上下文占用,主动刷一次(不等下一次 refreshMeta)
          api.context().then(setContext).catch(() => {})
          break
        }
        case 'usage': {
          // 增量累加做即时反馈;流结束后的 refreshMeta 会用 /api/usage 服务端真值对齐
          setUsage((prev) => {
            const next = {
              input_tokens: (prev?.input_tokens ?? 0) + Number(event['input_tokens'] ?? 0),
              output_tokens: (prev?.output_tokens ?? 0) + Number(event['output_tokens'] ?? 0),
              cache_read: (prev?.cache_read ?? 0) + Number(event['cache_read'] ?? 0),
              cache_creation: (prev?.cache_creation ?? 0) + Number(event['cache_creation'] ?? 0),
              llm_rounds: (prev?.llm_rounds ?? 0) + 1,
              total_tokens: 0,
              cache_hit: null as number | null,
            }
            const billed = next.input_tokens + next.cache_read + next.cache_creation
            next.total_tokens = billed + next.output_tokens
            next.cache_hit = billed > 0 ? next.cache_read / billed : null
            return next
          })
          break
        }
        case 'turn_usage': {
          for (let i = list.length - 1; i >= 0; i--) {
            const it = list[i]
            if (it.kind === 'turnEnd') {
              it.tokens = Number(event['total'] ?? 0)
              break
            }
          }
          break
        }
        case 'loop_complete': {
          settleRunning(list)
          list.push({
            kind: 'turnEnd',
            steps: Number(event['total_turns'] ?? 0),
            elapsed: runStartRef.current ? Date.now() - runStartRef.current : 0,
          })
          break
        }
        default:
          break
      }
      render()
    },
    [render],
  )

  const send = useCallback(
    async (text: string) => {
      if (running || !text.trim() || !isHome) return
      runStartRef.current = Date.now()
      itemsRef.current = [...itemsRef.current, { kind: 'user', text, ts: Date.now() }]
      setItems([...itemsRef.current])
      setRunning(true)
      try {
        await streamChat(text, handleEvent)
      } catch (e) {
        itemsRef.current = [
          ...itemsRef.current,
          { kind: 'error', message: e instanceof Error ? e.message : String(e) },
        ]
        setItems([...itemsRef.current])
      } finally {
        settleRunning(itemsRef.current)
        setItems([...itemsRef.current])
        setRunning(false)
        refreshMeta()
      }
    },
    [running, handleEvent, refreshMeta, isHome],
  )

  const abort = useCallback(async () => {
    try {
      await api.abort()
    } catch {
      /* ignore */
    }
  }, [])

  const newSession = useCallback(async () => {
    if (!isHome) return
    try {
      await api.newSession()
      itemsRef.current = []
      setItems([])
      await refreshMeta()
    } catch {
      /* ignore */
    }
  }, [isHome, refreshMeta])

  const resumeSession = useCallback(
    async (id: string) => {
      if (!isHome) return
      try {
        await api.resumeSession(id)
        await refreshMeta()
        await api.history().then((history) => {
          const restored = historyToItems(history)
          itemsRef.current = restored
          setItems([...restored])
        })
      } catch {
        /* ignore */
      }
    },
    [isHome, refreshMeta],
  )

  const answerPermission = useCallback(
    async (body: { allowed?: boolean; answer?: string }) => {
      if (!permission) return
      try {
        await api.answerPermission(permission.requestId, body)
      } catch {
        /* ignore */
      }
      setPermission(null)
    },
    [permission],
  )

  const addWorkspace = useCallback((path: string) => {
    const normalized = path.trim()
    if (!normalized) return
    setWorkspaces((prev) => {
      const exists = prev.some((p) => p.replace(/\\/g, '/').toLowerCase() === normalized.replace(/\\/g, '/').toLowerCase())
      const next = exists ? prev : [...prev, normalized]
      localStorage.setItem(WS_KEY, JSON.stringify(next))
      return next
    })
    localStorage.setItem(ACTIVE_WS_KEY, normalized)
    setActiveWorkspace(normalized)
  }, [])

  const switchWorkspace = useCallback((path: string) => {
    localStorage.setItem(ACTIVE_WS_KEY, path)
    setActiveWorkspace(path)
  }, [])

  // ── 三栏交互(设计 §11.3/§11.4)────────────────────────────────
  // 折叠/展开是离散切换:才给轨道过渡动画;拖拽与窗口缩放一律瞬时。
  const fireAnimating = useCallback(() => {
    setAnimating(true)
    window.setTimeout(() => setAnimating(false), 320)
  }, [])

  const toggleSidebarCollapsed = useCallback(() => {
    const next = !sidebarCollapsed
    setSidebarCollapsed(next)
    localStorage.setItem('ac-sidebar-collapsed', next ? '1' : '0')
    fireAnimating()
  }, [sidebarCollapsed, fireAnimating])

  const toggleFiles = useCallback(() => {
    const next = !filesOpen
    setFilesOpen(next)
    localStorage.setItem('ac-files-open', next ? '1' : '0')
    fireAnimating()
  }, [filesOpen, fireAnimating])

  // 拖拽基点冻结在手势开始时的渲染宽度(DSH:从存储偏好出发会让被夹住的列跳回)
  const sidebarBase = useRef(0)
  const filesBase = useRef(0)
  const onSidebarStart = useCallback(() => {
    sidebarBase.current = sidebarW
    setDragging(true)
  }, [sidebarW])
  const onSidebarDrag = useCallback((dx: number) => {
    const next = clampWidth(sidebarBase.current + dx, SIDEBAR_MIN, SIDEBAR_MAX)
    setSidebarW(next)
    localStorage.setItem('ac-sidebar-w', String(next))
  }, [])
  const onFilesStart = useCallback(() => {
    filesBase.current = filesW
    setDragging(true)
  }, [filesW])
  const onFilesDrag = useCallback((dx: number) => {
    const next = clampWidth(
      filesBase.current - dx,
      FILES_MIN,
      Math.max(FILES_MIN, window.innerWidth * FILES_MAX_RATIO),
    )
    setFilesW(next)
    localStorage.setItem('ac-files-w', String(next))
  }, [])
  const onDragEnd = useCallback(() => setDragging(false), [])

  // 会话改名/删除后的刷新:删除的是当前会话时后端已自动开新会话 → 清空对话区
  const handleSessionsChanged = useCallback(
    async (currentDeleted: boolean) => {
      if (currentDeleted) {
        itemsRef.current = []
        setItems([])
      }
      await refreshMeta()
    },
    [refreshMeta],
  )

  const isEmpty = items.length === 0
  const activeBaseName = activeWorkspace
    ? activeWorkspace.split(/[\\/]/).filter(Boolean).pop() ?? ''
    : ''

  return (
    <div
      className={styles.frame}
      style={{
        gridTemplateColumns: `${sidebarCollapsed ? SIDEBAR_COLLAPSED : sidebarW}px minmax(360px, 1fr) ${
          filesOpen ? `${filesW}px` : '0px'
        }`,
      }}
      data-dragging={dragging || undefined}
      data-animating={animating || undefined}
    >
      <div className={styles.sidebarCol}>
        <Sidebar
          sessions={sessions}
          state={state}
          workspaces={workspaces}
          activeWorkspace={activeWorkspace}
          collapsed={sidebarCollapsed}
          onToggleCollapse={toggleSidebarCollapsed}
          onNewSession={newSession}
          onResume={resumeSession}
          onOpenSettings={() => setSettingsOpen(true)}
          onAddWorkspace={addWorkspace}
          onSwitchWorkspace={switchWorkspace}
          onSessionsChanged={handleSessionsChanged}
        />
      </div>
      <div className={styles.mainColumn}>
        <div className={styles.mainHeader}>
          <span className={styles.mainTitle}>{activeBaseName || 'ArchCode'}</span>
          {!isHome && <span className={styles.mainBadge}>仅浏览</span>}
          <span className={styles.headerSpring} />
          <TaskMonitor />
          <button
            className={styles.iconBtn}
            onClick={toggleFiles}
            data-on={filesOpen || undefined}
            title={filesOpen ? '收起文件面板' : '打开文件面板'}
          >
            <svg width={16} height={16} viewBox="0 0 16 16" fill="none" aria-hidden>
              <rect x="2" y="2.5" width="12" height="11" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
              <path d="M10 2.5v11" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </button>
        </div>
        <div className={styles.scroll} ref={scrollRef} onScroll={onScroll} id="chat-scroll">
          {isEmpty ? (
            <Hero project={activeWorkspace} locked={!isHome} />
          ) : (
            <div className="contentColumn">
              <ChatItems items={items} />
            </div>
          )}
        </div>
        {detached && (
          <button className={styles.jumpBottom} onClick={jumpToBottom}>
            ↓ 回到底部
          </button>
        )}
        <div className={styles.composerSeat}>
          <div className={styles.composerStack}>
            <TodoPanel todos={todos} />
            <Composer
              running={running}
              disabled={!state || !isHome}
              usage={usage}
              permissionMode={state?.permission_mode ?? 'default'}
              planMode={state?.plan_mode ?? false}
              modelName={state?.model ?? ''}
              providers={providers}
              onSend={send}
              onAbort={abort}
              onNewSession={newSession}
              onModeChange={async (mode) => {
                await api.setPermissionMode(mode)
                await refreshMeta()
              }}
              onModelSwitch={async () => {
                await refreshMeta()
              }}
            />
          </div>
        </div>
        <StatusBar model={state?.model ?? ''} running={running} context={context} />
      </div>
      {filesOpen && serverDir && (
        <div className={styles.filesCol}>
          <FilePanel workDir={serverDir} showBrowsingHint={!isHome} />
        </div>
      )}
      {!sidebarCollapsed && (
        <DragHandle left={`${sidebarW}px`} onStart={onSidebarStart} onDrag={onSidebarDrag} onEnd={onDragEnd} />
      )}
      {filesOpen && (
        <DragHandle
          left={`calc(100% - ${filesW}px)`}
          onStart={onFilesStart}
          onDrag={onFilesDrag}
          onEnd={onDragEnd}
        />
      )}
      {permission && <PermissionDialog permission={permission} onAnswer={answerPermission} />}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}

/** 列宽拖拽手柄:跨在列边界上的浮层,pointer capture + rAF 节流(设计 §11.3)。
 *  capture 只是加固——窗口级监听才是手势主路(DSH:滚动容器会抢手势,合成指针会抛 NotFoundError)。 */
function DragHandle(props: {
  left: string
  onStart: () => void
  onDrag: (dx: number) => void
  onEnd: () => void
}) {
  const [active, setActive] = useState(false)
  const captured = useRef(false)
  const origin = useRef(0)
  const latest = useRef(0)
  const raf = useRef<number | null>(null)
  const listeners = useRef<{ move: (e: PointerEvent) => void; up: () => void } | null>(null)
  const cb = useRef(props)
  cb.current = props

  const reportMove = useCallback((clientX: number) => {
    latest.current = clientX
    if (raf.current === null) {
      raf.current = requestAnimationFrame(() => {
        raf.current = null
        cb.current.onDrag(latest.current - origin.current)
      })
    }
  }, [])

  const finish = useCallback(() => {
    if (!captured.current) return
    captured.current = false
    const l = listeners.current
    if (l) {
      window.removeEventListener('pointermove', l.move)
      window.removeEventListener('pointerup', l.up)
      window.removeEventListener('pointercancel', l.up)
      listeners.current = null
    }
    if (raf.current !== null) {
      cancelAnimationFrame(raf.current)
      raf.current = null
    }
    setActive(false)
    cb.current.onEnd()
  }, [])

  const start = useCallback(
    (clientX: number) => {
      origin.current = clientX
      latest.current = clientX
      setActive(true)
      cb.current.onStart()
      const move = (e: PointerEvent) => reportMove(e.clientX)
      const up = () => finish()
      listeners.current = { move, up }
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', up)
      window.addEventListener('pointercancel', up)
    },
    [reportMove, finish],
  )

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0 || captured.current) return
      e.preventDefault()
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        /* 无效指针(合成事件):窗口监听兜底 */
      }
      captured.current = true
      start(e.clientX)
    },
    [start],
  )

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!captured.current) return
      reportMove(e.clientX)
    },
    [reportMove],
  )

  return (
    <div
      className={styles.handle}
      style={{ left: props.left }}
      data-dragging={active || undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
    />
  )
}

/** Hero 态(新对话):居中 logo + 标题 + 工作区 chip(§10.2)。 */
function Hero({ project, locked }: { project: string; locked: boolean }) {
  const projectName = project ? project.split(/[\\/]/).filter(Boolean).pop() : ''
  return (
    <div className={styles.hero}>
      <div className={styles.heroStack}>
        <div className={styles.heroHeadline}>
          <svg width={30} height={30} viewBox="0 0 24 24" fill="none" aria-hidden>
            <rect x="2" y="2" width="20" height="20" rx="6" fill="var(--ac-label-primary)" />
            <path
              d="M8 9.5 L11.5 12 L8 14.5 M13 15 H16.5"
              stroke="var(--ac-bg-base)"
              strokeWidth="1.8"
              strokeLinecap="round"
              fill="none"
            />
          </svg>
          <span>ArchCode</span>
        </div>
        {projectName && (
          <button className={styles.heroChip} type="button" title={locked ? '该工作区未接入后端(浏览模式)' : project}>
            <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
              <path
                d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 2h4.5A1.5 1.5 0 0 1 14 6.5v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7z"
                stroke="currentColor"
                strokeWidth="1.2"
              />
            </svg>
            <span>{projectName}</span>
            {locked && <span className={styles.heroLock}>仅浏览</span>}
          </button>
        )}
      </div>
    </div>
  )
}
