import { useCallback, useEffect, useRef, useState } from 'react'
import { api, subscribeEvents } from './api'
import type { ContextInfo, Item, PermissionState, SessionInfo, TodoItem, UsageTotal } from './types'
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

// ── 三栏几何常量(设计 §11.2,数值可微调)──────────────────
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
function loadWorkspaces(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(WS_KEY) ?? '[]')
    return Array.isArray(raw) ? raw.filter((x) => typeof x === 'string') : []
  } catch {
    return []
  }
}

function clampWidth(px: number, min: number, max: number): number {
  return Math.min(Math.round(max), Math.max(min, Math.round(px)))
}

/** /api/history → 渲染条目(打开会话/完成刷新共用):工具行按 tool_use_id 配对回填。 */
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

const norm = (p: string) => p.replace(/\\/g, '/').toLowerCase()

export default function App() {
  const [items, setItems] = useState<Item[]>([])
  const [running, setRunning] = useState(false)
  const [permission, setPermission] = useState<PermissionState | null>(null)
  const [usage, setUsage] = useState<UsageTotal | null>(null)
  const [todos, setTodos] = useState<TodoItem[]>([])
  const [runningIds, setRunningIds] = useState<string[]>([])
  const [sessionsByWs, setSessionsByWs] = useState<Record<string, SessionInfo[]>>({})
  const [perm, setPerm] = useState({ mode: 'default', planMode: false })
  const [sessionModel, setSessionModel] = useState<{
    provider: string
    model: string
    override: boolean
  } | null>(null)
  const [modelName, setModelName] = useState('')
  const [providers, setProviders] = useState<
    Array<{ name: string; model: string; protocol: string }>
  >([])
  const [context, setContext] = useState<ContextInfo | null>(null)
  const [workspaces, setWorkspaces] = useState<string[]>(loadWorkspaces)
  // 同步镜像:异步回调(refreshSessions/addWorkspace)里读 ref,避开闭包捕获旧清单
  const workspacesRef = useRef<string[]>(workspaces)
  const [activeWorkspace, setActiveWorkspace] = useState(
    () => localStorage.getItem(ACTIVE_WS_KEY) ?? '',
  )
  // 当前打开的对话(草稿或已落盘)——多会话并行下"看哪里"与"跑哪里"解耦
  const [openSession, setOpenSession] = useState<{ id: string; workspace: string } | null>(
    null,
  )
  const [settingsOpen, setSettingsOpen] = useState(false)
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
  const unsubRef = useRef<(() => void) | null>(null)
  // 智能滚底(§13-A3):距底 >80px 视为"脱离底部",不再跟随
  const scrollRef = useRef<HTMLDivElement>(null)
  const detachedRef = useRef(false)
  const [detached, setDetached] = useState(false)

  const openRef = useRef(openSession)
  openRef.current = openSession
  const activeWsRef = useRef(activeWorkspace)
  activeWsRef.current = activeWorkspace

  const openWs = openSession?.workspace ?? activeWorkspace
  const openId = openSession?.id ?? null
  const openRunning = openId !== null && runningIds.includes(openId)

  /** itemsRef 变更后 rAF 批量刷新(高频 delta 不逐条 setState)。 */
  const render = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0
      setItems([...itemsRef.current])
    })
  }, [])

  const refreshSessions = useCallback(async () => {
    const list = workspacesRef.current
    const rows = await Promise.all(
      list.map(async (ws) => {
        try {
          return [ws, await api.sessionsByWorkspace(ws)] as const
        } catch {
          return [ws, [] as SessionInfo[]] as const
        }
      }),
    )
    const map: Record<string, SessionInfo[]> = {}
    for (const [ws, list] of rows) map[ws] = list
    setSessionsByWs(map)
  }, [])

  const refreshMeta = useCallback(async () => {
    try {
      const st = await api.state()
      setRunningIds(st.running_session_ids)
      const m = await api.model()
      setModelName(m.current)
      setProviders(m.providers)
      await refreshSessions()
      const sid = openRef.current?.id
      if (sid) {
        api.context(sid).then(setContext).catch(() => {})
        api.usage(sid).then(setUsage).catch(() => {})
        api.todo(sid).then((r) => setTodos(r.todos)).catch(() => {})
        api.permissionMode(sid).then((r) => setPerm({ mode: r.mode, planMode: r.plan_mode })).catch(() => {})
      } else {
        setContext(null)
        setUsage(null)
        setTodos([])
      }
    } catch {
      /* 服务端未就绪时静默 */
    }
  }, [refreshSessions])

  // 启动:把本地工作区清单注册到服务端,拉一次元数据
  useEffect(() => {
    ;(async () => {
      for (const ws of loadWorkspaces()) {
        try {
          await api.registerWorkspace(ws)
        } catch {
          /* 目录不存在等:保留在本地清单,切换时给出提示 */
        }
      }
      await refreshMeta()
    })()
    // 仅启动时执行一次
  }, [])

  // 无激活工作区时,默认选第一个
  useEffect(() => {
    if (!activeWorkspace && workspaces.length) setActiveWorkspace(workspaces[0])
  }, [workspaces, activeWorkspace])

  // 打开会话:加载其历史(静态快照;运行中的实时跟播为 v2)
  useEffect(() => {
    if (!openId) {
      itemsRef.current = []
      setItems([])
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const history = await api.history(openId)
        if (cancelled) return
        const restored = historyToItems(history)
        itemsRef.current = restored
        setItems([...restored])
      } catch {
        /* 草稿尚无历史:静默 */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [openId])

  // 打开会话的权限模式/计划模式/生效模型回显
  useEffect(() => {
    if (!openId) return
    api.permissionMode(openId).then((r) => setPerm({ mode: r.mode, planMode: r.plan_mode })).catch(() => {})
    api
      .sessionModel(openId)
      .then((r) => setSessionModel({ provider: r.provider, model: r.model, override: r.override }))
      .catch(() => {})
  }, [openId])

  // 打开的会话正在后台运行:轮询状态,完成时自动刷新历史
  const prevRunningRef = useRef(false)
  useEffect(() => {
    // 任意会话在跑就轮询(不限当前打开的):驱动侧栏转圈与完成检测
    if (runningIds.length === 0) {
      prevRunningRef.current = false
      return
    }
    const timer = setInterval(async () => {
      try {
        const st = await api.state()
        setRunningIds(st.running_session_ids)
      } catch {
        /* ignore */
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [runningIds.length > 0])
  useEffect(() => {
    if (prevRunningRef.current && !openRunning && openId) {
      ;(async () => {
        try {
          const history = await api.history(openId)
          itemsRef.current = historyToItems(history)
          setItems([...itemsRef.current])
        } catch {
          /* ignore */
        }
        await refreshMeta()
      })()
    }
    prevRunningRef.current = openRunning
  }, [openRunning, openId, refreshMeta])

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
    (event: { type: string; [k: string]: unknown }) => {
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
        case 'usage': {
          // 增量累加做即时反馈;完成后的 refreshMeta 用 /api/usage 真值对齐
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
          const sid = openRef.current?.id
          if (sid) api.context(sid).then(setContext).catch(() => {})
          break
        }
        case 'queued': {
          // 运行中入队的服务端回执(TUI 同款提示语义)
          const pos = Number(event['position'] ?? 0)
          const t = String(event['text'] ?? '')
          list.push({
            kind: 'notice',
            text: `已加入队列（第 ${pos} 位），当前任务完成后执行：${t}`,
          })
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
        case 'session_renamed': {
          // 草稿落盘换正式 id:后续 state/history/abort 都按新 id 寻址
          const newId = String(event['session_id'] ?? '')
          const cur = openRef.current
          if (newId && cur && cur.id !== newId) {
            setOpenSession({ id: newId, workspace: cur.workspace })
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

  const onRunEnd = useCallback(async () => {
    setRunning(false)
    unsubRef.current?.()
    unsubRef.current = null
    const sid = openRef.current?.id
    if (sid) {
      try {
        const history = await api.history(sid)
        itemsRef.current = historyToItems(history)
        setItems([...itemsRef.current])
      } catch {
        /* ignore */
      }
    }
    await refreshMeta()
  }, [refreshMeta])

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || !activeWorkspace) return
      const sid = openRef.current?.id
      // 排队路径(TUI 同款):会话运行中再发=入队,FIFO 批尾执行。
      // 不重复订阅/不置运行态;提示行由 wire 的 queued 事件带回来。
      if (sid && running) {
        try {
          await api.chat(sid, text)
        } catch (e) {
          itemsRef.current = [
            ...itemsRef.current,
            { kind: 'error', message: e instanceof Error ? e.message : String(e) },
          ]
          setItems([...itemsRef.current])
        }
        return
      }
      const ws = openRef.current?.workspace ?? activeWorkspace
      let runSid = sid
      try {
        if (!runSid) {
          const r = await api.newSession(ws)
          runSid = r.session_id
          setOpenSession({ id: runSid, workspace: ws })
        }
      } catch (e) {
        itemsRef.current = [
          ...itemsRef.current,
          { kind: 'error', message: e instanceof Error ? e.message : String(e) },
        ]
        setItems([...itemsRef.current])
        return
      }
      runStartRef.current = Date.now()
      itemsRef.current = [...itemsRef.current, { kind: 'user', text, ts: Date.now() }]
      setItems([...itemsRef.current])
      setRunning(true)
      unsubRef.current?.()
      unsubRef.current = subscribeEvents(runSid, handleEvent, () => {
        void onRunEnd()
      })
      try {
        await api.chat(runSid, text)
        // 乐观标记:转圈立即出现,不等 3s 轮询
        setRunningIds((prev) => (prev.includes(runSid) ? prev : [...prev, runSid]))
      } catch (e) {
        itemsRef.current = [
          ...itemsRef.current,
          { kind: 'error', message: e instanceof Error ? e.message : String(e) },
        ]
        setItems([...itemsRef.current])
        setRunning(false)
        unsubRef.current?.()
        unsubRef.current = null
      }
    },
    [running, activeWorkspace, handleEvent, onRunEnd],
  )

  const abort = useCallback(async () => {
    const sid = openRef.current?.id
    if (sid) {
      try {
        await api.abort(sid)
      } catch {
        /* ignore */
      }
    }
  }, [])

  const newSession = useCallback(async () => {
    if (!activeWorkspace) return
    try {
      const r = await api.newSession(activeWorkspace)
      unsubRef.current?.()
      unsubRef.current = null
      setOpenSession({ id: r.session_id, workspace: activeWorkspace })
      itemsRef.current = []
      setItems([])
      setRunning(false)
      setContext(null)
      setUsage(null)
      setTodos([])
      setPerm({ mode: 'default', planMode: false })
      setSessionModel(null)
      await refreshMeta()
    } catch {
      /* ignore */
    }
  }, [activeWorkspace, refreshMeta])

  /** 跨项目打开会话:切视图 + 确保运行时(从磁盘恢复)+ 历史加载由 openId 效应接管。 */
  const openWorkspaceSession = useCallback(
    async (ws: string, id: string) => {
      localStorage.setItem(ACTIVE_WS_KEY, ws)
      setActiveWorkspace(ws)
      try {
        await api.resume(id, ws)
      } catch (e) {
        alert(e instanceof Error ? e.message : String(e))
        return
      }
      setOpenSession({ id, workspace: ws })
      await refreshMeta()
    },
    [refreshMeta],
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

  const addWorkspace = useCallback(
    async (path: string) => {
      const normalized = path.trim()
      if (!normalized) return
      try {
        await api.registerWorkspace(normalized)
      } catch (e) {
        alert(e instanceof Error ? e.message : String(e))
        return
      }
      const cur = workspacesRef.current
      const exists = cur.some((p) => norm(p) === norm(normalized))
      const next = exists ? cur : [...cur, normalized]
      workspacesRef.current = next
      setWorkspaces(next)
      localStorage.setItem(WS_KEY, JSON.stringify(next))
      localStorage.setItem(ACTIVE_WS_KEY, normalized)
      setActiveWorkspace(normalized)
      await refreshMeta()
    },
    [refreshMeta],
  )

  const switchWorkspace = useCallback((path: string) => {
    // 工作区点击 = 切换"新对话的落点 + 侧栏视图";运行中的对话不受影响
    localStorage.setItem(ACTIVE_WS_KEY, path)
    setActiveWorkspace(path)
  }, [])

  // 会话改名/删除后的刷新:删除的是打开的会话时清空对话区
  const handleSessionsChanged = useCallback(
    async (deletedId: string | null) => {
      if (deletedId && openRef.current?.id === deletedId) {
        unsubRef.current?.()
        unsubRef.current = null
        setOpenSession(null)
        itemsRef.current = []
        setItems([])
      }
      await refreshMeta()
    },
    [refreshMeta],
  )

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

  // 拖拽基点冻结在手势开始时的渲染宽度
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
          sessionsByWs={sessionsByWs}
          runningIds={runningIds}
          openWorkspace={openWs}
          openSessionId={openId}
          workspaces={workspaces}
          activeWorkspace={activeWorkspace}
          collapsed={sidebarCollapsed}
          onToggleCollapse={toggleSidebarCollapsed}
          onNewSession={newSession}
          onOpenSession={openWorkspaceSession}
          onOpenSettings={() => setSettingsOpen(true)}
          onAddWorkspace={addWorkspace}
          onSwitchWorkspace={switchWorkspace}
          onSessionsChanged={handleSessionsChanged}
        />
      </div>
      <div className={styles.mainColumn}>
        <div className={styles.mainHeader}>
          <span className={styles.mainTitle}>{activeBaseName || 'ArchCode'}</span>
          <span className={styles.headerSpring} />
          <TaskMonitor sessionId={openId ?? ''} />
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
            <Hero project={activeWorkspace} />
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
              disabled={!activeWorkspace}
              workspace={openWs}
              usage={usage}
              context={context}
              permissionMode={perm.mode}
              planMode={perm.planMode}
              modelName={modelName}
              providers={providers}
              onSend={send}
              onAbort={abort}
              onNewSession={newSession}
              onModeChange={async (mode) => {
                const sid = openRef.current?.id
                if (!sid) return
                await api.setPermissionMode(sid, mode)
                await refreshMeta()
              }}
              sessionId={openId ?? ''}
              sessionModel={sessionModel}
              onSessionModelChange={(m) => setSessionModel(m)}
              onOpenSettings={() => setSettingsOpen(true)}
            />
          </div>
        </div>
        <StatusBar model={modelName} running={running} />
      </div>
      {filesOpen && openWs && (
        <div className={styles.filesCol}>
          <FilePanel workDir={openWs} />
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
      {settingsOpen && (
        <SettingsPanel
          onClose={() => setSettingsOpen(false)}
          workspace={activeWorkspace}
          sessionId={openId}
        />
      )}
    </div>
  )
}

/** 列宽拖拽手柄:跨在列边界上的浮层,pointer capture + rAF 节流(设计 §11.3)。
 *  capture 只是加固——窗口级监听才是手势主路(滚动容器会抢手势,合成指针会抛 NotFoundError)。 */
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
        raf.current = 0
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
function Hero({ project }: { project: string }) {
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
          <div className={styles.heroChip} title={project}>
            <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
              <path
                d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 2h4.5A1.5 1.5 0 0 1 14 6.5v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7z"
                stroke="currentColor"
                strokeWidth="1.2"
              />
            </svg>
            <span>{projectName}</span>
          </div>
        )}
      </div>
    </div>
  )
}
