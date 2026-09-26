import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import styles from './SettingsPanel.module.css'

type Scope = 'user' | 'project'
type Section = 'models' | 'mcp' | 'skills' | 'hooks' | 'permissions' | 'appearance'

const SECTIONS: Array<{ id: Section; label: string }> = [
  { id: 'models', label: '模型' },
  { id: 'mcp', label: 'MCP 服务器' },
  { id: 'skills', label: 'Skills' },
  { id: 'hooks', label: 'Hooks' },
  { id: 'permissions', label: '权限' },
  { id: 'appearance', label: '外观' },
]

/** 设置面板:800×560 Modal,作用域选择器 + 六节(§9.2)。 */
export default function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [scope, setScope] = useState<Scope>('project')
  const [section, setSection] = useState<Section>('models')
  const [data, setData] = useState<Record<string, unknown>>({})
  const [exists, setExists] = useState(true)
  const [notice, setNotice] = useState('')
  const [skills, setSkills] = useState<
    Array<{ name: string; description: string; source: string; path: string }>
  >([])
  const [mode, setMode] = useState('default')
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? 'light')

  const load = useCallback(async () => {
    try {
      const res = await api.getSettings(scope)
      setData(res.data ?? {})
      setExists(res.exists)
    } catch {
      setData({})
      setExists(false)
    }
  }, [scope])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    api.skills().then(setSkills).catch(() => {})
  }, [])

  const save = useCallback(
    async (key: 'providers' | 'mcp_servers' | 'hooks', items: unknown[]) => {
      try {
        const res = await api.putSettings(scope, key, items)
        await load()
        setNotice(
          res.restart_required ? '已写入,重启 ArchCode 后生效' : '已写入',
        )
        setTimeout(() => setNotice(''), 3000)
      } catch (e) {
        setNotice(e instanceof Error ? e.message : String(e))
      }
    },
    [scope, load],
  )

  const providers = (data['providers'] as Array<Record<string, unknown>>) ?? []
  const mcpServers = (data['mcp_servers'] as Array<Record<string, unknown>>) ?? []
  const hooks = (data['hooks'] as Array<Record<string, unknown>>) ?? []

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.panel} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <span className={styles.title}>设置</span>
          <select
            className={styles.scope}
            value={scope}
            onChange={(e) => setScope(e.target.value as Scope)}
          >
            <option value="user">用户</option>
            <option value="project">项目</option>
          </select>
          <span className={styles.notice}>{notice}</span>
          <button className={styles.closeBtn} onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>
        <div className={styles.body}>
          <nav className={styles.nav}>
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                className={`${styles.navItem} ${section === s.id ? styles.navActive : ''}`}
                onClick={() => setSection(s.id)}
              >
                {s.label}
              </button>
            ))}
          </nav>
          <div className={styles.content}>
            {!exists && <div className={styles.hint}>该作用域尚无 config.yaml,保存时将创建。</div>}

            {section === 'models' && (
              <ProviderSection items={providers} onSave={(items) => save('providers', items)} />
            )}
            {section === 'mcp' && (
              <McpSection items={mcpServers} onSave={(items) => save('mcp_servers', items)} />
            )}
            {section === 'skills' && (
              <div>
                {skills.length === 0 && <div className={styles.hint}>未加载任何技能</div>}
                {skills.map((s) => (
                  <div key={s.name} className={styles.row}>
                    <div className={styles.rowMain}>
                      <div className={styles.rowTitle}>/{s.name}</div>
                      <div className={styles.rowDesc}>{s.description}</div>
                    </div>
                    <span className={styles.tag}>{s.source}</span>
                  </div>
                ))}
                <div className={styles.hint}>编辑技能请直接修改 SKILL.md 文件。</div>
              </div>
            )}
            {section === 'hooks' && (
              <div>
                {hooks.length === 0 && <div className={styles.hint}>未声明任何 hook</div>}
                {hooks.map((h, i) => (
                  <div key={i} className={styles.row}>
                    <div className={styles.rowMain}>
                      <div className={styles.rowTitle}>
                        {String(h['id'] ?? `#${i}`)} · {String(h['event'] ?? '')}
                      </div>
                      <div className={styles.rowDesc}>
                        {String((h['action'] as Record<string, unknown>)?.['type'] ?? '')}
                        {h['reject'] ? ' · reject' : ''}
                        {h['once'] ? ' · once' : ''}
                      </div>
                    </div>
                    <span className={styles.tag}>{String(h['__source__'] ?? '')}</span>
                  </div>
                ))}
                <div className={styles.hint}>编辑 hooks 请修改 config.yaml(改动需重启生效)。</div>
              </div>
            )}
            {section === 'permissions' && (
              <div>
                <div className={styles.modeRow}>
                  {['default', 'accept', 'bypass'].map((m) => (
                    <button
                      key={m}
                      className={`${styles.modeChip} ${mode === m ? styles.modeChipActive : ''}`}
                      onClick={async () => {
                        setMode(m)
                        await api.setPermissionMode(m)
                      }}
                    >
                      {m}
                    </button>
                  ))}
                </div>
                <div className={styles.hint}>
                  default:写操作需确认;accept:自动接受;bypass:全部放行。即时生效。
                </div>
              </div>
            )}
            {section === 'appearance' && (
              <div className={styles.modeRow}>
                {['light', 'dark'].map((t) => (
                  <button
                    key={t}
                    className={`${styles.modeChip} ${theme === t ? styles.modeChipActive : ''}`}
                    onClick={() => {
                      setTheme(t)
                      document.documentElement.dataset.theme = t
                      localStorage.setItem('ac-theme', t)
                    }}
                  >
                    {t === 'light' ? '浅色' : '深色'}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** Provider 行列表 + 添加表单(§9.1 ModelsSection 的极简版)。 */
function ProviderSection({
  items,
  onSave,
}: {
  items: Array<Record<string, unknown>>
  onSave: (items: unknown[]) => void
}) {
  const [form, setForm] = useState({ name: '', protocol: 'openai-compat', base_url: '', model: '', api_key: '' })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))
  return (
    <div>
      {items.map((p, i) => (
        <div key={i} className={styles.row}>
          <div className={styles.rowMain}>
            <div className={styles.rowTitle}>
              {String(p['name'] ?? '')} · {String(p['model'] ?? '')}
            </div>
            <div className={styles.rowDesc}>
              {String(p['protocol'] ?? '')} · {String(p['base_url'] ?? '')}
            </div>
          </div>
          <span className={styles.tag}>{p['api_key'] ? 'key ✓' : 'key 缺失'}</span>
          <button
            className={styles.deleteBtn}
            onClick={() => onSave(items.filter((_, j) => j !== i))}
          >
            删除
          </button>
        </div>
      ))}
      <div className={styles.addForm}>
        <input className={styles.input} placeholder="名称" value={form.name} onChange={(e) => set('name', e.target.value)} />
        <select className={styles.input} value={form.protocol} onChange={(e) => set('protocol', e.target.value)}>
          <option value="openai-compat">openai-compat</option>
          <option value="openai">openai</option>
          <option value="anthropic">anthropic</option>
        </select>
        <input className={styles.input} placeholder="base_url" value={form.base_url} onChange={(e) => set('base_url', e.target.value)} />
        <input className={styles.input} placeholder="模型名" value={form.model} onChange={(e) => set('model', e.target.value)} />
        <input className={styles.input} placeholder="API Key" type="password" value={form.api_key} onChange={(e) => set('api_key', e.target.value)} />
        <button
          className={styles.addBtn}
          disabled={!form.name || !form.base_url || !form.model}
          onClick={() => {
            onSave([...items, { ...form, max_output_tokens: 16384 }])
            setForm({ name: '', protocol: 'openai-compat', base_url: '', model: '', api_key: '' })
          }}
        >
          添加
        </button>
      </div>
    </div>
  )
}

/** MCP 行列表 + 添加表单(stdio/http 二选一)。 */
function McpSection({
  items,
  onSave,
}: {
  items: Array<Record<string, unknown>>
  onSave: (items: unknown[]) => void
}) {
  const [form, setForm] = useState({ name: '', command: '', url: '' })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))
  return (
    <div>
      {items.map((s, i) => {
        const isHttp = Boolean(s['url'])
        return (
          <div key={i} className={styles.row}>
            <div className={styles.rowMain}>
              <div className={styles.rowTitle}>{String(s['name'] ?? '')}</div>
              <div className={styles.rowDesc}>
                {isHttp ? String(s['url']) : `${String(s['command'] ?? '')} ${(s['args'] as string[])?.join(' ') ?? ''}`}
              </div>
            </div>
            <span className={styles.tag}>{isHttp ? 'http' : 'stdio'}</span>
            <button
              className={styles.deleteBtn}
              onClick={() => onSave(items.filter((_, j) => j !== i))}
            >
              删除
            </button>
          </div>
        )
      })}
      <div className={styles.addForm}>
        <input className={styles.input} placeholder="名称" value={form.name} onChange={(e) => set('name', e.target.value)} />
        <input className={styles.input} placeholder="stdio: command" value={form.command} onChange={(e) => set('command', e.target.value)} />
        <input className={styles.input} placeholder="或 http: url" value={form.url} onChange={(e) => set('url', e.target.value)} />
        <button
          className={styles.addBtn}
          disabled={!form.name || (!form.command && !form.url)}
          onClick={() => {
            const entry = form.url
              ? { name: form.name, url: form.url }
              : { name: form.name, command: form.command, args: [] }
            onSave([...items, entry])
            setForm({ name: '', command: '', url: '' })
          }}
        >
          添加
        </button>
      </div>
    </div>
  )
}
