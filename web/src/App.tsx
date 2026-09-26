import { useCallback, useEffect, useRef, useState } from 'react'
import { api, streamChat } from './api'
import type { AgentState, Item, PermissionState, SessionInfo, Usage, WireEvent } from './types'
import Sidebar from './components/Sidebar'
import Composer from './components/Composer'
import PermissionDialog from './components/PermissionDialog'
import StatusBar from './components/StatusBar'
import SettingsPanel from './components/SettingsPanel'
import { ChatItems } from './components/ChatItems'
import styles from './App.module.css'

const WS_KEY = 'ac-workspaces'
const ACTIVE_WS_KEY = 'ac-active-workspace'

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
  const [usage, setUsage] = useState<Usage | null>(null)
  const [state, setState] = useState<AgentState | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [providers, setProviders] = useState<
    Array<{ name: string; model: string; protocol: string }>
  >([])
  const [workspaces, setWorkspaces] = useState<string[]>(loadWorkspaces)
  const [activeWorkspace, setActiveWorkspace] = useState(
    () => localStorage.getItem(ACTIVE_WS_KEY) ?? '',
  )
  const itemsRef = useRef<Item[]>([])
  const rafRef = useRef(0)

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
          const restored: Item[] = []
          for (const m of history) {
            const role = m['role']
            const content = String(m['content'] ?? '')
            if (role === 'user' && content) restored.push({ kind: 'user', text: content })
            if (role === 'assistant' && content)
              restored.push({ kind: 'assistant', text: content, running: false })
          }
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

  // 新条目自动滚底(v1 恒跟随;DSH 的 near-bottom 检测后补)
  useEffect(() => {
    const el = document.getElementById('chat-scroll')
    if (el) el.scrollTop = el.scrollHeight
  }, [items])

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
            list.push({ kind: 'assistant', text, running: true })
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
          setUsage({
            inputTokens: Number(event['input_tokens'] ?? 0),
            outputTokens: Number(event['output_tokens'] ?? 0),
            cacheRead: Number(event['cache_read'] ?? 0),
            cacheCreation: Number(event['cache_creation'] ?? 0),
          })
          break
        }
        case 'loop_complete': {
          for (const item of list)
            if (item.kind !== 'user' && item.kind !== 'error') item.running = false
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
      itemsRef.current = [...itemsRef.current, { kind: 'user', text }]
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
        for (const item of itemsRef.current)
          if (item.kind !== 'user' && item.kind !== 'error') item.running = false
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
          const restored: Item[] = []
          for (const m of history) {
            const role = m['role']
            const content = String(m['content'] ?? '')
            if (role === 'user' && content) restored.push({ kind: 'user', text: content })
            if (role === 'assistant' && content)
              restored.push({ kind: 'assistant', text: content, running: false })
          }
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

  const isEmpty = items.length === 0
  const activeBaseName = activeWorkspace
    ? activeWorkspace.split(/[\\/]/).filter(Boolean).pop() ?? ''
    : ''

  return (
    <div className={styles.frame}>
      <Sidebar
        sessions={sessions}
        state={state}
        workspaces={workspaces}
        activeWorkspace={activeWorkspace}
        onNewSession={newSession}
        onResume={resumeSession}
        onOpenSettings={() => setSettingsOpen(true)}
        onAddWorkspace={addWorkspace}
        onSwitchWorkspace={switchWorkspace}
      />
      <div className={styles.mainColumn}>
        <div className={styles.scroll} id="chat-scroll">
          {isEmpty ? (
            <Hero project={activeWorkspace} locked={!isHome} />
          ) : (
            <div className="contentColumn">
              <ChatItems items={items} />
            </div>
          )}
        </div>
        <div className={styles.composerSeat}>
          <Composer
            running={running}
            disabled={!state || !isHome}
            usage={usage}
            permissionMode={state?.permission_mode ?? 'default'}
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
        <StatusBar model={state?.model ?? ''} running={running} />
      </div>
      {permission && <PermissionDialog permission={permission} onAnswer={answerPermission} />}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </div>
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
