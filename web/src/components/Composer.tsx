import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { fmtInt, fmtPercent, fmtTokens } from '../format'
import type { ContextInfo, UsageTotal } from '../types'
import ContextMeter from './ContextMeter'
import styles from './Composer.module.css'

interface Props {
  running: boolean
  disabled: boolean
  usage: UsageTotal | null
  context: ContextInfo | null
  permissionMode: string
  planMode: boolean
  modelName: string
  providers: Array<{ name: string; model: string; protocol: string }>
  onSend: (text: string) => void
  onAbort: () => void
  onNewSession: () => void
  onModeChange: (mode: string) => void
  onModelSwitch: (name: string) => void
}

interface MenuEntry {
  name: string
  desc: string
  kind: 'command' | 'skill'
}

const MODES = ['default', 'accept', 'bypass'] as const

/** 输入卡:编辑器(14 行封顶 + / 触发指令列表)+ 控制行(§10.3/§10.7)。 */
export default function Composer({
  running,
  disabled,
  usage,
  context,
  permissionMode,
  planMode,
  modelName,
  providers,
  onSend,
  onAbort,
  onNewSession,
  onModeChange,
  onModelSwitch,
}: Props) {
  const [draft, setDraft] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [menuQuery, setMenuQuery] = useState('') // slash 触发时 = "/" 后的过滤词
  const [atOpen, setAtOpen] = useState(false)
  const [atQuery, setAtQuery] = useState('') // @ 后的文件名过滤词
  const [atItems, setAtItems] = useState<string[]>([])
  const [modelOpen, setModelOpen] = useState(false)
  const [modeOpen, setModeOpen] = useState(false)
  const [usageOpen, setUsageOpen] = useState(false)
  const [currentModel, setCurrentModel] = useState(modelName)
  const [skills, setSkills] = useState<Array<{ name: string; description: string }>>([])
  const editorRef = useRef<HTMLTextAreaElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.skills().then(setSkills).catch(() => {})
  }, [])

  useEffect(() => {
    setCurrentModel(modelName)
  }, [modelName])

  useEffect(() => {
    const el = editorRef.current
    if (!el) return
    el.style.height = 'auto'
    // 14 行封顶(14 × 22px),之下自增高
    el.style.height = `${Math.min(el.scrollHeight, 14 * 22)}px`
  }, [draft])

  // 编辑器外点击关闭所有弹出菜单
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
        setAtOpen(false)
        setModelOpen(false)
        setModeOpen(false)
        setUsageOpen(false)
      }
    }
    window.addEventListener('pointerdown', onDown)
    return () => window.removeEventListener('pointerdown', onDown)
  }, [])

  const onDraftChange = (value: string) => {
    setDraft(value)
    // "/"(开头)→ 指令列表;"@词"(行内)→ 文件引用菜单(§13-B1);两者互斥
    if (value.startsWith('/')) {
      setMenuQuery(value.slice(1))
      setMenuOpen(true)
      setAtOpen(false)
      return
    }
    const atMatch = /(^|\s)@([^\s@]*)$/.exec(value)
    if (atMatch) {
      setAtQuery(atMatch[2])
      setAtOpen(true)
      setMenuOpen(false)
      return
    }
    if (menuOpen && menuQuery !== '') {
      setMenuOpen(false)
      setMenuQuery('')
    }
    if (atOpen) setAtOpen(false)
  }

  // @ 菜单防抖搜索(§13-B1)
  useEffect(() => {
    if (!atOpen) return
    const timer = setTimeout(() => {
      api
        .filesSearch(atQuery)
        .then((r) => setAtItems(r.results))
        .catch(() => setAtItems([]))
    }, 300)
    return () => clearTimeout(timer)
  }, [atOpen, atQuery])

  /** 指令 + 技能的合并清单(slash 模式按 query 过滤)。 */
  const menuItems = useMemo(() => {
    const commands: MenuEntry[] = [
      { name: 'plan', desc: '进入或退出计划模式', kind: 'command' },
      { name: 'new', desc: '新建会话', kind: 'command' },
    ]
    const skillEntries: MenuEntry[] = skills.map((s) => ({
      name: s.name,
      desc: s.description,
      kind: 'skill',
    }))
    const all = [...commands, ...skillEntries]
    const q = menuQuery.toLowerCase()
    return q ? all.filter((m) => m.name.toLowerCase().includes(q)) : all
  }, [menuQuery, skills])

  const pickEntry = (entry: MenuEntry) => {
    setMenuOpen(false)
    setMenuQuery('')
    if (entry.kind === 'command' && entry.name === 'new') {
      onNewSession()
      setDraft('')
      editorRef.current?.focus()
      return
    }
    // /plan 与技能:插入文本,Enter 发送(服务端 /plan 拦截;技能由 LLM 解析)
    setDraft(`/${entry.name} `)
    editorRef.current?.focus()
  }

  /** 选中 @ 文件:替换草稿末尾的 @token 为相对路径 + 空格。 */
  const pickFile = (path: string) => {
    setAtOpen(false)
    setAtQuery('')
    setDraft((prev) => prev.replace(/@([^\s@]*)$/, `${path} `))
    editorRef.current?.focus()
  }

  const submit = () => {
    if (running || !draft.trim()) return
    onSend(draft)
    setDraft('')
    setMenuOpen(false)
    setMenuQuery('')
    setAtOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setMenuOpen(false)
      setAtOpen(false)
      setModelOpen(false)
      setModeOpen(false)
      setUsageOpen(false)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (menuOpen && draft.startsWith('/') && menuItems.length > 0) {
        // slash 列表打开时,Enter 选中第一项(而非发送)
        pickEntry(menuItems[0])
        return
      }
      if (atOpen && atItems.length > 0) {
        pickFile(atItems[0])
        return
      }
      submit()
    }
  }

  return (
    <div className={styles.root} ref={rootRef}>
      <div className={styles.editor}>
        <textarea
          ref={editorRef}
          rows={1}
          value={draft}
          placeholder={running ? 'Agent 工作中…' : '输入消息,/ 指令与技能,@ 引用文件'}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
        />
      </div>

      {/* slash / + 触发的指令列表(DSH MenuView:全宽贴卡底) */}
      {menuOpen && (
        <div className={styles.menu} role="listbox">
          <div className={styles.menuSection}>指令</div>
          {menuItems.filter((m) => m.kind === 'command').map((m) => (
            <button key={m.name} className={styles.menuItem} onClick={() => pickEntry(m)}>
              <span className={styles.menuName}>/{m.name}</span>
              <span className={styles.menuDesc}>{m.desc}</span>
            </button>
          ))}
          {menuItems.filter((m) => m.kind === 'command').length === 0 && (
            <div className={styles.menuEmpty}>无匹配指令</div>
          )}
          <div className={styles.menuSection}>技能</div>
          {menuItems.filter((m) => m.kind === 'skill').map((m) => (
            <button key={m.name} className={styles.menuItem} onClick={() => pickEntry(m)}>
              <span className={styles.menuName}>/{m.name}</span>
              <span className={styles.menuDesc}>{m.desc}</span>
            </button>
          ))}
          {menuItems.filter((m) => m.kind === 'skill').length === 0 && (
            <div className={styles.menuEmpty}>{menuQuery ? '无匹配技能' : '未加载任何技能'}</div>
          )}
        </div>
      )}

      {/* @ 触发的文件引用菜单(§13-B1):全宽贴卡底,与 slash 菜单同骨架 */}
      {atOpen && (
        <div className={styles.menu} role="listbox">
          <div className={styles.menuSection}>文件</div>
          {atItems.map((p) => (
            <button key={p} className={styles.menuItem} onClick={() => pickFile(p)} title={p}>
              <span className={styles.menuName}>{p.split('/').pop()}</span>
              <span className={styles.menuDesc}>{p}</span>
            </button>
          ))}
          {atItems.length === 0 && (
            <div className={styles.menuEmpty}>{atQuery ? '无匹配文件' : '输入文件名搜索…'}</div>
          )}
        </div>
      )}

      <div className={styles.row}>
        <div className={styles.tools}>
          <button
            className={styles.toolBtn}
            aria-label="指令与技能"
            aria-expanded={menuOpen}
            onClick={() => {
              setMenuQuery('')
              setMenuOpen((v) => !v)
            }}
            disabled={disabled}
          >
            +
          </button>
          {planMode && (
            <span className={styles.planChip} title="Plan 模式开启中：只读探索并产出计划，输入 /plan 退出">
              Plan
            </span>
          )}
          <div className={styles.menuAnchor}>
            <button
              className={styles.modeChip}
              aria-expanded={modeOpen}
              onClick={() => setModeOpen((v) => !v)}
              disabled={disabled}
              title="权限模式"
            >
              {permissionMode}
              <svg width={10} height={10} viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M3 6l5 5 5-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
            </button>
            {modeOpen && (
              <div className={styles.modeMenu}>
                {MODES.map((m) => (
                  <button
                    key={m}
                    className={styles.modeItem}
                    onClick={() => {
                      onModeChange(m)
                      setModeOpen(false)
                    }}
                  >
                    <span className={styles.menuName}>{m}</span>
                    {m === permissionMode && (
                      <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
                        <path
                          d="M3 8.5l3.5 3.5L13 5"
                          stroke="currentColor"
                          strokeWidth="1.6"
                          strokeLinecap="round"
                        />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className={styles.trailing}>
          <ContextMeter context={context} cacheHit={usage?.cache_hit ?? null} />
          <div className={styles.menuAnchor}>
            {usage && usage.total_tokens > 0 && (
              <button
                className={styles.usageChip}
                aria-expanded={usageOpen}
                onClick={() => setUsageOpen((v) => !v)}
                title="会话累计用量"
              >
                {fmtTokens(usage.total_tokens)} tok
              </button>
            )}
            {usageOpen && usage && (
              <div className={styles.usageMenu}>
                <div className={styles.menuSection}>会话累计</div>
                <div className={styles.usageGrid}>
                  <span>输入(未缓存)</span>
                  <span>{fmtInt(usage.input_tokens)}</span>
                  <span>缓存读</span>
                  <span>{fmtInt(usage.cache_read)}</span>
                  <span>缓存写</span>
                  <span>{fmtInt(usage.cache_creation)}</span>
                  <span>输出</span>
                  <span>{fmtInt(usage.output_tokens)}</span>
                </div>
                <div className={styles.usageFoot}>
                  缓存命中 {fmtPercent(usage.cache_hit)} · LLM {usage.llm_rounds} 轮
                </div>
              </div>
            )}
          </div>
          <div className={styles.menuAnchor}>
            <button
              className={styles.modelChip}
              aria-expanded={modelOpen}
              onClick={() => setModelOpen((v) => !v)}
              disabled={disabled}
              title="切换模型"
            >
              {currentModel || '模型'}
              <svg width={10} height={10} viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M3 6l5 5 5-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
            </button>
            {modelOpen && (
              <div className={styles.modelMenu}>
                <div className={styles.menuSection}>模型</div>
                {providers.length === 0 && (
                  <div className={styles.menuEmpty}>未配置 provider(设置中添加)</div>
                )}
                {providers.map((p) => (
                  <button
                    key={p.name}
                    className={styles.modelItem}
                    onClick={async () => {
                      try {
                        await api.switchModel(p.name)
                        setCurrentModel(p.model)
                        setModelOpen(false)
                      } catch (e) {
                        alert(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    <span className={styles.menuName}>{p.model}</span>
                    <span className={styles.menuDesc}>{p.protocol}</span>
                    {p.model === currentModel && (
                      <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
                        <path
                          d="M3 8.5l3.5 3.5L13 5"
                          stroke="currentColor"
                          strokeWidth="1.6"
                          strokeLinecap="round"
                        />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
          {running ? (
            <button
              className={`${styles.primaryBtn} ${styles.stopBtn}`}
              aria-label="停止"
              onClick={onAbort}
            >
              <svg viewBox="0 0 16 16" width={15} height={15} aria-hidden>
                <rect x={3} y={3} width={10} height={10} rx={3} fill="currentColor" />
              </svg>
            </button>
          ) : (
            <button
              className={styles.primaryBtn}
              aria-label="发送"
              disabled={running || !draft.trim()}
              onClick={submit}
            >
              <svg viewBox="0 0 16 16" width={15} height={15} aria-hidden>
                <path
                  d="M8 14V2M8 2L3 7M8 2l5 5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  fill="none"
                />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
