import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import styles from './SettingsPanel.module.css'

type Scope = 'user' | 'project'
type Section = 'models' | 'mcp' | 'skills' | 'agents' | 'hooks' | 'permissions' | 'appearance'

const SECTIONS: Array<{ id: Section; label: string }> = [
  { id: 'models', label: '模型' },
  { id: 'mcp', label: 'MCP 服务器' },
  { id: 'skills', label: 'Skills' },
  { id: 'agents', label: '子 Agent' },
  { id: 'hooks', label: 'Hooks' },
  { id: 'permissions', label: '权限' },
  { id: 'appearance', label: '外观' },
]

export interface AgentDefInfo {
  agent_type: string
  when_to_use: string
  source: string
  path: string
  model: string
  max_turns: number
  permission_mode: string
  background: boolean
  tools: string[]
  disallowed_tools: string[]
}

/** 设置面板:800×560 Modal,作用域选择器 + 七节(§9.2)。 */
export default function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [scope, setScope] = useState<Scope>('project')
  const [section, setSection] = useState<Section>('models')
  const [data, setData] = useState<Record<string, unknown>>({})
  const [userData, setUserData] = useState<Record<string, unknown>>({})
  const [exists, setExists] = useState(true)
  const [notice, setNotice] = useState('')
  const [skills, setSkills] = useState<
    Array<{ name: string; description: string; source: string; path: string; is_directory: boolean }>
  >([])
  const [agents, setAgents] = useState<AgentDefInfo[]>([])
  // 主从视图:列表 → 详情(详情态存主键,切区块即清空)
  const [skillDetail, setSkillDetail] = useState<string | null>(null)
  const [agentDetail, setAgentDetail] = useState<string | null>(null)
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
    // 另一层(user/project)只读展示:当前作用域没配的条目,标注来源可见
    const otherScope = scope === 'user' ? 'project' : 'user'
    try {
      const res2 = await api.getSettings(otherScope)
      setUserData(res2.data ?? {})
    } catch {
      setUserData({})
    }
  }, [scope])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    api.skills().then(setSkills).catch(() => {})
    api.agents().then(setAgents).catch(() => {})
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
                onClick={() => {
                  setSection(s.id)
                  setSkillDetail(null)
                  setAgentDetail(null)
                }}
              >
                {s.label}
              </button>
            ))}
          </nav>
          <div className={styles.content}>
            {!exists && <div className={styles.hint}>该作用域尚无 config.yaml,保存时将创建。</div>}

            {section === 'models' && (
              <ProviderSection
                items={providers}
                readOnlyItems={(userData['providers'] as Array<Record<string, unknown>>) ?? []}
                onSave={(items) => save('providers', items)}
              />
            )}
            {section === 'mcp' && (
              <McpSection
                items={mcpServers}
                readOnlyItems={(userData['mcp_servers'] as Array<Record<string, unknown>>) ?? []}
                onSave={(items) => save('mcp_servers', items)}
              />
            )}
            {section === 'skills' && (() => {
              const detail = skills.find((s) => s.name === skillDetail)
              if (detail) return <SkillDetail skill={detail} onBack={() => setSkillDetail(null)} />
              return (
                <div>
                  {skills.length === 0 && <div className={styles.hint}>未加载任何技能</div>}
                  <div className={styles.listHead}>已加载 {skills.length} 个</div>
                  {skills.map((s) => (
                    <button
                      key={s.name}
                      type="button"
                      className={`${styles.row} ${styles.rowClickable}`}
                      onClick={() => setSkillDetail(s.name)}
                    >
                      <div className={styles.rowMain}>
                        <div className={styles.rowTitle}>/{s.name}</div>
                        <div className={styles.rowDesc}>{s.description}</div>
                      </div>
                      <span className={styles.tag}>{sourceLabel(s.source)}</span>
                      <span className={styles.rowChevron}>›</span>
                    </button>
                  ))}
                  <div className={styles.hint}>
                    点击条目查看详情;编辑技能请修改对应 SKILL.md 文件。
                  </div>
                </div>
              )
            })()}
            {section === 'agents' && (() => {
              const detail = agents.find((a) => a.agent_type === agentDetail)
              if (detail) return <AgentDetail agent={detail} onBack={() => setAgentDetail(null)} />
              return (
                <div>
                  {agents.length === 0 && (
                    <div className={styles.hint}>未加载任何子 agent 定义(内置定义缺失?)</div>
                  )}
                  <div className={styles.listHead}>生效定义 {agents.length} 个</div>
                  {agents.map((a) => (
                    <button
                      key={a.agent_type}
                      type="button"
                      className={`${styles.row} ${styles.rowClickable}`}
                      onClick={() => setAgentDetail(a.agent_type)}
                    >
                      <div className={styles.rowMain}>
                        <div className={styles.rowTitle}>{a.agent_type}</div>
                        <div className={styles.rowDesc}>{a.when_to_use}</div>
                      </div>
                      <span className={styles.tag}>{sourceLabel(a.source)}</span>
                      <span className={styles.tag}>{a.background ? '后台' : '前台'}</span>
                      <span className={styles.rowChevron}>›</span>
                    </button>
                  ))}
                  <div className={styles.hint}>
                    添加子 agent:在 项目 `.archcode/agents/&lt;名字&gt;.md`(或用户级同名目录)创建
                    Markdown 定义——frontmatter 写 name / description / tools / maxTurns /
                    permissionMode / background,正文即该子 agent 的 system prompt;
                    新任务边界自动重载生效。
                  </div>
                </div>
              )
            })()}
            {section === 'hooks' && (
              <div>
                {hooks.length === 0 && <div className={styles.hint}>当前作用域未声明 hook</div>}
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
  readOnlyItems,
  onSave,
}: {
  items: Array<Record<string, unknown>>
  readOnlyItems: Array<Record<string, unknown>>
  onSave: (items: unknown[]) => void
}) {
  const [form, setForm] = useState({ name: '', protocol: 'openai-compat', base_url: '', model: '', api_key: '' })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))
  return (
    <div>
      {items.length === 0 && readOnlyItems.length > 0 && (
        <div className={styles.hint}>当前作用域未配置,以下为生效的 用户级 配置(只读,编辑请切换作用域)。</div>
      )}
      {readOnlyItems.map((p, i) => (
        <div key={`ro-${i}`} className={styles.row}>
          <div className={styles.rowMain}>
            <div className={styles.rowTitle}>
              {String(p['name'] ?? '')} · {String(p['model'] ?? '')}
            </div>
            <div className={styles.rowDesc}>
              {String(p['protocol'] ?? '')} · {String(p['base_url'] ?? '')}
            </div>
          </div>
          <span className={styles.tag}>{p['api_key'] ? 'key ✓' : 'key 缺失'}</span>
          <span className={styles.tag}>用户级</span>
        </div>
      ))}
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
  readOnlyItems,
  onSave,
}: {
  items: Array<Record<string, unknown>>
  readOnlyItems: Array<Record<string, unknown>>
  onSave: (items: unknown[]) => void
}) {
  // 编辑态:列表 ↔ 表单两个视图;null = 列表,'new' = 新建,数字 = 编辑第 idx 项
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [form, setForm] = useState({ name: '', type: 'stdio', command: '', args: '', url: '' })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const startEdit = (idx: number | 'new') => {
    setEditing(idx)
    if (idx === 'new') {
      setForm({ name: '', type: 'stdio', command: '', args: '', url: '' })
      return
    }
    const s = items[idx] as Record<string, unknown>
    const isHttp = Boolean(s['url'])
    setForm({
      name: String(s['name'] ?? ''),
      type: isHttp ? 'http' : 'stdio',
      command: String(s['command'] ?? ''),
      args: ((s['args'] as string[]) ?? []).join(' '),
      url: String(s['url'] ?? ''),
    })
  }

  if (editing !== null) {
    const isNew = editing === 'new'
    const canSave =
      form.name.trim() !== '' &&
      (form.type === 'http' ? form.url.trim() !== '' : form.command.trim() !== '')
    return (
      <div>
        <button type="button" className={styles.backBtn} onClick={() => setEditing(null)}>
          ‹ 返回列表
        </button>
        <div className={styles.detailTitle}>{isNew ? '新建 MCP 服务器' : `编辑 · ${form.name}`}</div>
        <div className={styles.editForm}>
          <label className={styles.fieldLabel}>名称</label>
          <input
            className={styles.input}
            placeholder="如 ocr-tool"
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
          />
          <label className={styles.fieldLabel}>类型</label>
          <select
            className={styles.input}
            value={form.type}
            onChange={(e) => set('type', e.target.value)}
          >
            <option value="stdio">stdio(本地命令)</option>
            <option value="http">http(远程服务)</option>
          </select>
          {form.type === 'stdio' ? (
            <>
              <label className={styles.fieldLabel}>命令</label>
              <input
                className={styles.input}
                placeholder="如 python"
                value={form.command}
                onChange={(e) => set('command', e.target.value)}
              />
              <label className={styles.fieldLabel}>参数(空格分隔)</label>
              <input
                className={styles.input}
                placeholder="如 server.py --port 8080"
                value={form.args}
                onChange={(e) => set('args', e.target.value)}
              />
            </>
          ) : (
            <>
              <label className={styles.fieldLabel}>URL</label>
              <input
                className={styles.input}
                placeholder="如 http://127.0.0.1:8000/mcp"
                value={form.url}
                onChange={(e) => set('url', e.target.value)}
              />
            </>
          )}
        </div>
        <div className={styles.editActions}>
          {!isNew && (
            <button
              type="button"
              className={styles.deleteBtn}
              onClick={() => {
                onSave(items.filter((_, j) => j !== editing))
                setEditing(null)
              }}
            >
              删除
            </button>
          )}
          <span className={styles.spring} />
          <button type="button" className={styles.cancelBtn} onClick={() => setEditing(null)}>
            取消
          </button>
          <button
            type="button"
            className={styles.addBtn}
            disabled={!canSave}
            onClick={() => {
              const entry =
                form.type === 'http'
                  ? { name: form.name.trim(), url: form.url.trim() }
                  : {
                      name: form.name.trim(),
                      command: form.command.trim(),
                      args: form.args.trim() ? form.args.trim().split(/\s+/) : [],
                    }
              const next = isNew
                ? [...items, entry]
                : items.map((it, j) => (j === editing ? entry : it))
              onSave(next)
              setEditing(null)
            }}
          >
            保存
          </button>
        </div>
        <div className={styles.hint}>保存写入 {`{作用域}`} config.yaml;MCP 变更需重启生效。</div>
      </div>
    )
  }

  return (
    <div>
      {items.length === 0 && readOnlyItems.length > 0 && (
        <div className={styles.hint}>当前作用域未配置,以下为生效的 用户级 配置(只读)。</div>
      )}
      {items.length === 0 && readOnlyItems.length === 0 && (
        <div className={styles.hint}>当前作用域未配置 MCP 服务器。</div>
      )}
      <div className={styles.listHeadRow}>
        <span className={styles.listHead}>已配置 {items.length} 个</span>
        <button type="button" className={styles.addBtn} onClick={() => startEdit('new')}>
          + 新建
        </button>
      </div>
      {readOnlyItems.map((s, i) => {
        const isHttp = Boolean(s['url'])
        return (
          <div key={`ro-${i}`} className={styles.row}>
            <div className={styles.rowMain}>
              <div className={styles.rowTitle}>{String(s['name'] ?? '')}</div>
              <div className={styles.rowDesc}>
                {isHttp
                  ? String(s['url'])
                  : `${String(s['command'] ?? '')} ${(s['args'] as string[])?.join(' ') ?? ''}`}
              </div>
            </div>
            <span className={styles.tag}>{isHttp ? 'http' : 'stdio'}</span>
            <span className={styles.tag}>用户级</span>
          </div>
        )
      })}
      {items.map((s, i) => {
        const isHttp = Boolean(s['url'])
        return (
          <button
            key={i}
            type="button"
            className={`${styles.row} ${styles.rowClickable}`}
            onClick={() => startEdit(i)}
          >
            <div className={styles.rowMain}>
              <div className={styles.rowTitle}>{String(s['name'] ?? '')}</div>
              <div className={styles.rowDesc}>
                {isHttp
                  ? String(s['url'])
                  : `${String(s['command'] ?? '')} ${(s['args'] as string[])?.join(' ') ?? ''}`}
              </div>
            </div>
            <span className={styles.tag}>{isHttp ? 'http' : 'stdio'}</span>
            <span className={styles.rowChevron}>›</span>
          </button>
        )
      })}
      <div className={styles.hint}>点击条目编辑;变更需重启 ArchCode 生效。</div>
    </div>
  )
}

function sourceLabel(source: string): string {
  if (source === 'user') return '用户级'
  if (source === 'project') return '项目级'
  if (source === 'builtin') return '内置'
  return source
}

/** 详情视图:技能(描述全文/来源/类型/文件路径 + 复制)。 */
function SkillDetail({
  skill,
  onBack,
}: {
  skill: { name: string; description: string; source: string; path: string; is_directory: boolean }
  onBack: () => void
}) {
  return (
    <div>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        ‹ 返回列表
      </button>
      <div className={styles.detailTitle}>/{skill.name}</div>
      <div className={styles.detailGrid}>
        <span className={styles.detailLabel}>描述</span>
        <span className={styles.detailValue}>{skill.description || '(无描述)'}</span>
        <span className={styles.detailLabel}>来源</span>
        <span className={styles.detailValue}>{sourceLabel(skill.source)}</span>
        <span className={styles.detailLabel}>类型</span>
        <span className={styles.detailValue}>{skill.is_directory ? '目录型技能' : '单文件技能'}</span>
        <span className={styles.detailLabel}>文件路径</span>
        <span className={styles.detailValue}>
          <code className={styles.mono}>{skill.path}</code>
          <CopyMini text={skill.path} />
        </span>
      </div>
      <div className={styles.hint}>
        编辑技能请修改对应 SKILL.md 文件,改动在新任务边界生效。
      </div>
    </div>
  )
}

/** 详情视图:子 agent 定义(全部 frontmatter 字段 + 工具清单 + 路径)。 */
function AgentDetail({ agent, onBack }: { agent: AgentDefInfo; onBack: () => void }) {
  return (
    <div>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        ‹ 返回列表
      </button>
      <div className={styles.detailTitle}>{agent.agent_type}</div>
      <div className={styles.detailGrid}>
        <span className={styles.detailLabel}>选用依据</span>
        <span className={styles.detailValue}>{agent.when_to_use || '(无描述)'}</span>
        <span className={styles.detailLabel}>来源</span>
        <span className={styles.detailValue}>{sourceLabel(agent.source)}</span>
        <span className={styles.detailLabel}>模型</span>
        <span className={styles.detailValue}>
          {agent.model && agent.model !== 'inherit' ? agent.model : '沿用主对话模型'}
        </span>
        <span className={styles.detailLabel}>轮次预算</span>
        <span className={styles.detailValue}>最多 {agent.max_turns} 轮</span>
        <span className={styles.detailLabel}>权限模式</span>
        <span className={styles.detailValue}>{agent.permission_mode}</span>
        <span className={styles.detailLabel}>运行方式</span>
        <span className={styles.detailValue}>{agent.background ? '后台运行' : '前台(阻塞)'}</span>
        <span className={styles.detailLabel}>工具白名单</span>
        <span className={styles.detailValue}>
          {agent.tools.length > 0 ? agent.tools.join('、') : '不限制(全部工具)'}
        </span>
        {agent.disallowed_tools.length > 0 && (
          <>
            <span className={styles.detailLabel}>禁用工具</span>
            <span className={styles.detailValue}>{agent.disallowed_tools.join('、')}</span>
          </>
        )}
        {agent.path && (
          <>
            <span className={styles.detailLabel}>文件路径</span>
            <span className={styles.detailValue}>
              <code className={styles.mono}>{agent.path}</code>
              <CopyMini text={agent.path} />
            </span>
          </>
        )}
      </div>
      <div className={styles.hint}>
        修改定义请编辑对应 Markdown 文件,新任务边界自动重载生效。
      </div>
    </div>
  )
}

/** 复制小按钮(路径旁,带成功态)。 */
function CopyMini({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className={styles.copyMini}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        } catch {
          /* 剪贴板不可用时静默 */
        }
      }}
    >
      {copied ? '已复制' : '复制'}
    </button>
  )
}
