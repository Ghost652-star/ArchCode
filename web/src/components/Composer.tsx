import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Usage } from '../types'
import styles from './Composer.module.css'

interface Props {
  running: boolean
  disabled: boolean
  usage: Usage | null
  permissionMode: string
  modelName: string
  providers: Array<{ name: string; model: string; protocol: string }>
  onSend: (text: string) => void
  onAbort: () => void
  onModeChange: (mode: string) => void
  onModelSwitch: (name: string) => void
}

/** 输入卡:编辑器(14 行封顶)+ 控制行(+ 指令/技能 · 权限 · 模型chip · 发送⇄停止)。 */
export default function Composer({
  running,
  disabled,
  usage,
  permissionMode,
  modelName,
  providers,
  onSend,
  onAbort,
  onModeChange,
  onModelSwitch,
}: Props) {
  const [draft, setDraft] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [modelOpen, setModelOpen] = useState(false)
  const [currentModel, setCurrentModel] = useState(modelName)
  const [skills, setSkills] = useState<
    Array<{ name: string; description: string; source: string }>
  >([])
  const editorRef = useRef<HTMLTextAreaElement>(null)

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

  const submit = () => {
    if (running || !draft.trim()) return
    onSend(draft)
    setDraft('')
    setMenuOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const insert = (text: string) => {
    setDraft((d) => (d ? `${d.trimEnd()} ${text} ` : `${text} `))
    setMenuOpen(false)
    editorRef.current?.focus()
  }

  const pickSkill = (name: string) => insert(`/${name}`)

  return (
    <div className={styles.root}>
      <div className={styles.editor}>
        <textarea
          ref={editorRef}
          rows={1}
          value={draft}
          placeholder={
            running ? 'Agent 工作中…' : '输入消息,Enter 发送,Shift+Enter 换行'
          }
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
        />
      </div>
      <div className={styles.row}>
        <div className={styles.tools}>
          <div className={styles.menuAnchor}>
            <button
              className={styles.toolBtn}
              aria-label="指令与技能"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
              disabled={disabled}
            >
              +
            </button>
            {menuOpen && (
              <div className={styles.menu} role="menu">
                <div className={styles.menuLabel}>指令</div>
                <button
                  className={styles.menuItem}
                  onClick={() => {
                    insert('/plan')
                  }}
                >
                  <span className={styles.menuName}>/plan</span>
                  <span className={styles.menuDesc}>进入或退出计划模式</span>
                </button>
                <div className={styles.menuLabel}>技能</div>
                {skills.length === 0 && (
                  <div className={styles.menuEmpty}>未加载任何技能</div>
                )}
                {skills.map((s) => (
                  <button
                    key={s.name}
                    className={styles.menuItem}
                    onClick={() => pickSkill(s.name)}
                    title={s.description}
                  >
                    <span className={styles.menuName}>/{s.name}</span>
                    <span className={styles.menuDesc}>{s.description}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <select
            className={styles.modeSelect}
            value={permissionMode}
            onChange={(e) => onModeChange(e.target.value)}
            disabled={disabled}
            title="权限模式"
          >
            <option value="default">default</option>
            <option value="accept">accept</option>
            <option value="bypass">bypass</option>
          </select>
        </div>
        <div className={styles.trailing}>
          <span className={styles.usage}>
            {usage ? `${usage.inputTokens + usage.outputTokens} tok` : ''}
          </span>
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
                <path
                  d="M3 6l5 5 5-5"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
              </svg>
            </button>
            {modelOpen && (
              <div className={styles.modelMenu}>
                <div className={styles.menuLabel}>模型</div>
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
              <svg viewBox="0 0 16 16" width={14} height={14} aria-hidden>
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
              <svg viewBox="0 0 16 16" width={14} height={14} aria-hidden>
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
