import { useEffect, useRef, useState } from 'react'
import type { Usage } from '../types'
import { api } from '../api'
import styles from './Composer.module.css'

interface Props {
  running: boolean
  disabled: boolean
  usage: Usage | null
  permissionMode: string
  onSend: (text: string) => void
  onAbort: () => void
  onModeChange: (mode: string) => void
}

/** 输入卡:编辑器(14 行封顶)+ 控制行(+ / 权限 / 模型 / meter / 发送⇄停止)(§10.3)。 */
export default function Composer({
  running,
  disabled,
  usage,
  permissionMode,
  onSend,
  onAbort,
  onModeChange,
}: Props) {
  const [draft, setDraft] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [skills, setSkills] = useState<
    Array<{ name: string; description: string; source: string }>
  >([])
  const editorRef = useRef<HTMLTextAreaElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.skills().then(setSkills).catch(() => {})
  }, [])

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

  const pickSkill = (name: string) => {
    setDraft((d) => (d ? `${d.trim()} /${name} ` : `/${name} `))
    setMenuOpen(false)
    editorRef.current?.focus()
  }

  const pickFile = () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.txt,.md,.py,.js,.ts,.json,.yaml,.yml,.toml,.css,.html'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      const content = await file.text()
      setDraft(
        (d) => `${d}${d ? '\n' : ''}[附件 ${file.name}]\n${content.slice(0, 20000)}\n[/附件]\n`,
      )
      setMenuOpen(false)
      editorRef.current?.focus()
    }
    input.click()
  }

  return (
    <div className={styles.root} ref={cardRef}>
      <div className={styles.editor}>
        <textarea
          ref={editorRef}
          rows={1}
          value={draft}
          placeholder={running ? 'Agent 工作中…(Esc 不可用,用停止按钮)' : '输入消息,Enter 发送,Shift+Enter 换行'}
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
              aria-label="添加"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
              disabled={disabled}
            >
              +
            </button>
            {menuOpen && (
              <div className={styles.menu} role="menu">
                <div className={styles.menuLabel}>附件</div>
                <button className={styles.menuItem} onClick={pickFile}>
                  📎 上传文本文件(内联进消息)
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
                    <span className={styles.menuSkillName}>/{s.name}</span>
                    <span className={styles.menuSkillDesc}>{s.description}</span>
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
          <span className={styles.usage} title="本轮 token 用量">
            {usage ? `${usage.inputTokens + usage.outputTokens} tok` : ''}
          </span>
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
