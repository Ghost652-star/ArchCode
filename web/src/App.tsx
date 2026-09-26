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

export default function App() {
  const [items, setItems] = useState<Item[]>([])
  const [running, setRunning] = useState(false)
  const [permission, setPermission] = useState<PermissionState | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [state, setState] = useState<AgentState | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const itemsRef = useRef<Item[]>([])
  const rafRef = useRef(0)

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
      setSessions(await api.sessions())
    } catch {
      /* 服务端未就绪时静默 */
    }
  }, [])

  useEffect(() => {
    refreshMeta()
  }, [refreshMeta])

  // 新条目自动滚底(v1 恒跟随;DSH 的 near-bottom 检测后补)
  useEffect(() => {
    const el = document.getElementById('chat-scroll')
    if (el) el.scrollTop = el.scrollHeight
  }, [items])

  /** 刷新页面/切换会话:读回历史重绘(v1 简化映射)。 */
  const restoreHistory = useCallback(async () => {
    try {
      const history = await api.history()
      const restored: Item[] = []
      for (const m of history) {
        const role = m['role']
        const content = String(m['content'] ?? '')
        if (role === 'user' && content) restored.push({ kind: 'user', text: content })
        if (role === 'assistant' && content) restored.push({ kind: 'assistant', text: content, running: false })
      }
      itemsRef.current = restored
      setItems([...restored])
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    restoreHistory()
  }, [restoreHistory])

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
          for (const item of list) if (item.kind !== 'user' && item.kind !== 'error') item.running = false
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
      if (running || !text.trim()) return
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
    [running, handleEvent, refreshMeta],
  )

  const abort = useCallback(async () => {
    try {
      await api.abort()
    } catch {
      /* ignore */
    }
  }, [])

  const newSession = useCallback(async () => {
    try {
      await api.newSession()
      itemsRef.current = []
      setItems([])
      await refreshMeta()
    } catch {
      /* ignore */
    }
  }, [refreshMeta])

  const resumeSession = useCallback(
    async (id: string) => {
      try {
        await api.resumeSession(id)
        await refreshMeta()
        await restoreHistory()
      } catch {
        /* ignore */
      }
    },
    [refreshMeta, restoreHistory],
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

  const isEmpty = items.length === 0

  return (
    <div className={styles.frame}>
      <Sidebar
        sessions={sessions}
        state={state}
        onNewSession={newSession}
        onResume={resumeSession}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <div className={styles.mainColumn}>
        <div className={styles.scroll} id="chat-scroll">
          {isEmpty ? (
            <Hero project={state?.work_dir ?? ''} />
          ) : (
            <div className="contentColumn">
              <ChatItems items={items} />
            </div>
          )}
        </div>
        <div className={styles.composerSeat}>
          <Composer
            running={running}
            disabled={!state}
            onSend={send}
            onAbort={abort}
            usage={usage}
            permissionMode={state?.permission_mode ?? 'default'}
            onModeChange={async (mode) => {
              await api.setPermissionMode(mode)
              await refreshMeta()
            }}
          />
        </div>
        <StatusBar
          model={state?.model ?? ''}
          workDir={state?.work_dir ?? ''}
          running={running}
        />
      </div>
      {permission && <PermissionDialog permission={permission} onAnswer={answerPermission} />}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </div>
  )
}

/** Hero 态(新对话):居中 logo + 标题 + 项目 chip(§10.2)。 */
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
          <button className={styles.heroChip} type="button">
            <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
              <path
                d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 2h4.5A1.5 1.5 0 0 1 14 6.5v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7z"
                stroke="currentColor"
                strokeWidth="1.2"
              />
            </svg>
            <span>{projectName}</span>
          </button>
        )}
      </div>
    </div>
  )
}
