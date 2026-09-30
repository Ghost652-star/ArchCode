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
  system_prompt: string
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
  // 主从视图:列表 → 详情/编辑(详情态存主键,切区块即清空)
  const [skillDetail, setSkillDetail] = useState<string | null>(null)
  const [agentDetail, setAgentDetail] = useState<string | null>(null)
  const [agentEdit, setAgentEdit] = useState<string | 'new' | null>(null)
  const [hookEdit, setHookEdit] = useState<number | 'new' | null>(null)
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
    api.state().then((s) => setMode(s.permission_mode ?? 'default')).catch(() => {})
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
                  setAgentEdit(null)
                  setHookEdit(null)
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
                scope={scope}
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
              if (agentEdit !== null) {
                const editing = agentEdit === 'new' ? null : agents.find((a) => a.agent_type === agentEdit) ?? null
                return (
                  <AgentEditForm
                    initial={editing}
                    scope={scope}
                    onBack={() => setAgentEdit(null)}
                    onSaved={(msg) => {
                      api.agents().then(setAgents).catch(() => {})
                      setNotice(msg)
                      window.setTimeout(() => setNotice(''), 4000)
                      setAgentEdit(null)
                    }}
                  />
                )
              }
              const detail = agents.find((a) => a.agent_type === agentDetail)
              if (detail) return <AgentDetail agent={detail} onBack={() => setAgentDetail(null)} />
              return (
                <div>
                  {agents.length === 0 ? (
                    <div className={styles.emptyState}>
                      <div className={styles.emptyTitle}>没有找到子 agent 定义</div>
                      <div className={styles.emptyDesc}>
                        填写名称、工具白名单和系统提示词,保存后写入定义文件。
                      </div>
                      <button type="button" className={styles.addBtn} onClick={() => setAgentEdit('new')}>
                        + 新建
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className={styles.listHeadRow}>
                        <span className={styles.listHead}>生效定义 {agents.length} 个</span>
                        <button type="button" className={styles.addBtn} onClick={() => setAgentEdit('new')}>
                          + 新建
                        </button>
                      </div>
                      {agents.map((a) => (
                        <button
                          key={a.agent_type}
                          type="button"
                          className={`${styles.row} ${styles.rowClickable}`}
                          onClick={() => {
                            if (a.source === 'builtin') setAgentDetail(a.agent_type)
                            else setAgentEdit(a.agent_type)
                          }}
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
                    </>
                  )}
                  <div className={styles.hint}>
                    点击可编辑(内置定义只读);新建写入 当前作用域 的 `.archcode/agents/&lt;名字&gt;.md`,
                    重启 ArchCode 后生效。
                  </div>
                </div>
              )
            })()}
            {section === 'hooks' && (() => {
              if (hookEdit !== null) {
                return (
                  <HookEditForm
                    hooks={hooks}
                    editing={hookEdit}
                    scope={scope}
                    onSave={(items) => {
                      save('hooks', items)
                      setHookEdit(null)
                    }}
                    onBack={() => setHookEdit(null)}
                  />
                )
              }
              return (
                <div>
                  <div className={styles.listHeadRow}>
                    <span className={styles.listHead}>当前作用域声明 {hooks.length} 条</span>
                    <button type="button" className={styles.addBtn} onClick={() => setHookEdit('new')}>
                      + 新建钩子
                    </button>
                  </div>
                  {hooks.length === 0 && (
                    <div className={styles.hint}>当前作用域未声明 hook</div>
                  )}
                  {hooks.map((h, i) => {
                    const action = (h['action'] as Record<string, unknown>) ?? {}
                    const type = String(action['type'] ?? '')
                    const main =
                      type === 'command'
                        ? String(action['command'] ?? '')
                        : type === 'http'
                          ? String(action['url'] ?? '')
                          : type === 'prompt'
                            ? String(action['message'] ?? '')
                            : String(action['prompt'] ?? '')
                    return (
                      <button
                        key={i}
                        type="button"
                        className={`${styles.row} ${styles.rowClickable}`}
                        onClick={() => setHookEdit(i)}
                      >
                        <div className={styles.rowMain}>
                          <div className={styles.rowTitle}>
                            {String(h['id'] ?? `#${i}`)} · {String(h['event'] ?? '')}
                          </div>
                          <div className={styles.rowDesc}>
                            {type} · {main}
                            {h['if'] ? ` · if: ${String(h['if'])}` : ''}
                          </div>
                        </div>
                        <span className={styles.tag}>{String(h['__source__'] ?? '')}</span>
                        {h['reject'] ? <span className={styles.tag}>reject</span> : null}
                        {h['once'] ? <span className={styles.tag}>once</span> : null}
                        <span className={styles.rowChevron}>›</span>
                      </button>
                    )
                  })}
                  <div className={styles.hint}>
                    8 类生命周期事件 × 4 种执行方式(命令/注入提示/HTTP/子 agent);保存写入当前作用域
                    config.yaml,重启 ArchCode 后生效。条件用 if 表达式,留空 = 总是触发。
                  </div>
                </div>
              )
            })()}
            {section === 'permissions' && (
              <div>
                <div className={styles.formCard}>
                  <div className={styles.permList}>
                    {(
                      [
                        ['default', '默认', '写操作需要逐次确认,读取不受限'],
                        ['accept', '自动接受', '写操作自动通过,不再逐次确认(谨慎使用)'],
                        ['bypass', '全部放行', '不做任何确认,风险自担'],
                      ] as Array<[string, string, string]>
                    ).map(([m, title, desc]) => (
                      <button
                        key={m}
                        type="button"
                        className={`${styles.permRow} ${mode === m ? styles.permRowActive : ''}`}
                        onClick={async () => {
                          setMode(m)
                          await api.setPermissionMode(m)
                        }}
                      >
                        <span className={styles.permRadio} data-on={mode === m || undefined} />
                        <div className={styles.rowMain}>
                          <div className={styles.rowTitle}>
                            {m} · {title}
                          </div>
                          <div className={styles.rowDesc}>{desc}</div>
                        </div>
                        {mode === m && (
                          <svg width={15} height={15} viewBox="0 0 16 16" fill="none" aria-hidden>
                            <path
                              d="M3 8.5l3.5 3.5L13 5"
                              stroke="var(--ac-brand-500)"
                              strokeWidth="1.8"
                              strokeLinecap="round"
                            />
                          </svg>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
                <div className={styles.hint}>
                  切换即时生效,composer 右下角的模式标签与此处联动。
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
/** 供应商管理:列表 ↔ 编辑表单(字段对应 ProviderConfig,重启生效)。 */
function ProviderSection({
  items,
  readOnlyItems,
  scope,
  onSave,
}: {
  items: Array<Record<string, unknown>>
  readOnlyItems: Array<Record<string, unknown>>
  scope: string
  onSave: (items: unknown[]) => void
}) {
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [form, setForm] = useState({
    name: '',
    protocol: 'openai-compat',
    base_url: '',
    model: '',
    api_key: '',
    max_output_tokens: '16384',
    context_window: '0',
    thinking: false,
  })
  const set = (k: string, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }))

  const startEdit = (idx: number | 'new') => {
    setEditing(idx)
    if (idx === 'new') {
      setForm({
        name: '',
        protocol: 'openai-compat',
        base_url: '',
        model: '',
        api_key: '',
        max_output_tokens: '16384',
        context_window: '0',
        thinking: false,
      })
      return
    }
    const p = items[idx] as Record<string, unknown>
    setForm({
      name: String(p['name'] ?? ''),
      protocol: String(p['protocol'] ?? 'openai-compat'),
      base_url: String(p['base_url'] ?? ''),
      model: String(p['model'] ?? ''),
      api_key: String(p['api_key'] ?? ''),
      max_output_tokens: String(p['max_output_tokens'] ?? 16384),
      context_window: String(p['context_window'] ?? 0),
      thinking: Boolean(p['thinking']),
    })
  }

  if (editing !== null) {
    const isNew = editing === 'new'
    const canSave =
      form.name.trim() !== '' && form.base_url.trim() !== '' && form.model.trim() !== ''
    const save = () => {
      const entry: Record<string, unknown> = {
        ...(isNew ? {} : (items[editing] as Record<string, unknown>)),
        name: form.name.trim(),
        protocol: form.protocol,
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        api_key: form.api_key.trim(),
        max_output_tokens: parseInt(form.max_output_tokens, 10) || 16384,
        context_window: parseInt(form.context_window, 10) || 0,
        thinking: form.thinking,
      }
      onSave(isNew ? [...items, entry] : items.map((p, j) => (j === editing ? entry : p)))
      setEditing(null)
    }
    return (
      <div>
        <button type="button" className={styles.backBtn} onClick={() => setEditing(null)}>
          ‹ 返回列表
        </button>
        <div className={styles.detailTitle}>
          {isNew ? '添加供应商' : `编辑 · ${form.name}`}
        </div>
        <div className={styles.formCard}>
          <div className={styles.formScope}>作用域: {scope === 'project' ? '项目' : '用户'}</div>
          <div className={styles.fieldRow}>
            <div>
              <label className={styles.fieldLabel}>名称</label>
              <input
                className={styles.input}
                placeholder="如 deepseek"
                value={form.name}
                disabled={!isNew}
                onChange={(e) => set('name', e.target.value)}
              />
            </div>
            <div>
              <label className={styles.fieldLabel}>协议</label>
              <select
                className={styles.input}
                value={form.protocol}
                onChange={(e) => set('protocol', e.target.value)}
              >
                <option value="openai-compat">openai-compat</option>
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
              </select>
            </div>
          </div>
          <label className={styles.fieldLabel}>base_url</label>
          <input
            className={styles.input}
            placeholder="如 https://api.deepseek.com"
            value={form.base_url}
            onChange={(e) => set('base_url', e.target.value)}
          />
          <label className={styles.fieldLabel}>模型名</label>
          <input
            className={styles.input}
            placeholder="如 deepseek-v4-flash"
            value={form.model}
            onChange={(e) => set('model', e.target.value)}
          />
          <label className={styles.fieldLabel}>API Key</label>
          <input
            className={styles.input}
            type="password"
            placeholder="留空则读取环境变量"
            value={form.api_key}
            onChange={(e) => set('api_key', e.target.value)}
          />
          <div className={styles.fieldRow}>
            <div>
              <label className={styles.fieldLabel}>最大输出 tokens</label>
              <input
                className={styles.input}
                type="number"
                min={1024}
                value={form.max_output_tokens}
                onChange={(e) => set('max_output_tokens', e.target.value)}
              />
            </div>
            <div>
              <label className={styles.fieldLabel}>上下文窗口(0 = 默认 128k)</label>
              <input
                className={styles.input}
                type="number"
                min={0}
                value={form.context_window}
                onChange={(e) => set('context_window', e.target.value)}
              />
            </div>
          </div>
          <div className={styles.toggleRow}>
            <span className={styles.toggleLabel}>开启思考模式(thinking,模型需支持)</span>
            <input
              type="checkbox"
              checked={form.thinking}
              onChange={(e) => set('thinking', e.target.checked)}
            />
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
              onClick={save}
            >
              保存
            </button>
          </div>
        </div>
        <div className={styles.hint}>
          保存写入当前作用域 config.yaml 的 providers 列表;重启 ArchCode 后生效。
        </div>
      </div>
    )
  }

  return (
    <div>
      {items.length === 0 && readOnlyItems.length > 0 && (
        <div className={styles.hint}>当前作用域未配置,以下为生效的 用户级 配置(只读,编辑请切换作用域)。</div>
      )}
      <div className={styles.listHeadRow}>
        <span className={styles.listHead}>当前作用域已配置 {items.length} 个</span>
        <button type="button" className={styles.addBtn} onClick={() => startEdit('new')}>
          + 添加供应商
        </button>
      </div>
      {items.length === 0 && readOnlyItems.length === 0 && (
        <div className={styles.emptyState}>
          <div className={styles.emptyTitle}>还没有配置供应商</div>
          <div className={styles.emptyDesc}>填写名称、协议、base_url 和模型名,保存后重启生效。</div>
        </div>
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
        <button
          key={i}
          type="button"
          className={`${styles.row} ${styles.rowClickable}`}
          onClick={() => startEdit(i)}
        >
          <div className={styles.rowMain}>
            <div className={styles.rowTitle}>
              {String(p['name'] ?? '')} · {String(p['model'] ?? '')}
            </div>
            <div className={styles.rowDesc}>
              {String(p['protocol'] ?? '')} · {String(p['base_url'] ?? '')}
            </div>
          </div>
          <span className={styles.tag}>{p['api_key'] ? 'key ✓' : 'key 缺失'}</span>
          <span className={styles.rowChevron}>›</span>
        </button>
      ))}
      {items.length > 0 && (
        <div className={styles.hint}>点击条目编辑;变更需重启 ArchCode 生效。</div>
      )}
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
  const [form, setForm] = useState({ name: '', type: 'stdio', command: '', args: '', url: '', env: '' })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const startEdit = (idx: number | 'new') => {
    setEditing(idx)
    if (idx === 'new') {
      setForm({ name: '', type: 'stdio', command: '', args: '', url: '', env: '' })
      return
    }
    const s = items[idx] as Record<string, unknown>
    const isHttp = Boolean(s['url'])
    const env = String((s['env'] as Record<string, unknown>) ? Object.entries(s['env'] as Record<string, unknown>).map(([k, v]) => `${k}=${String(v)}`).join('\n') : '')
    setForm({
      name: String(s['name'] ?? ''),
      type: isHttp ? 'http' : 'stdio',
      command: String(s['command'] ?? ''),
      args: ((s['args'] as string[]) ?? []).join(' '),
      url: String(s['url'] ?? ''),
      env,
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
              <label className={styles.fieldLabel}>环境变量(可选,每行 KEY=VALUE)</label>
              <textarea
                className={styles.input}
                rows={3}
                placeholder={'如 API_KEY=sk-xxx'}
                value={form.env}
                onChange={(e) => set('env', e.target.value)}
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
                      env: Object.fromEntries(
                        form.env
                          .split('\n')
                          .map((l) => l.trim())
                          .filter((l) => l.includes('='))
                          .map((l) => {
                            const eq = l.indexOf('=')
                            return [l.slice(0, eq).trim(), l.slice(eq + 1).trim()]
                          }),
                      ),
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
        编辑技能请修改对应 SKILL.md 文件,重启 ArchCode 后生效。
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
        修改定义请编辑对应 Markdown 文件,或直接在界面新建/编辑同目录定义,重启 ArchCode 后生效。
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

/** 新建/编辑子 agent:表单字段严格对应 AgentDef 的 frontmatter + 正文。 */
function AgentEditForm({
  initial,
  scope,
  onSaved,
  onBack,
}: {
  initial: AgentDefInfo | null
  scope: string
  onSaved: (msg: string) => void
  onBack: () => void
}) {
  const isNew = initial === null
  const [name, setName] = useState(initial?.agent_type ?? '')
  const [whenToUse, setWhenToUse] = useState(initial?.when_to_use ?? '')
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? '')
  const [tools, setTools] = useState((initial?.tools ?? []).join(', '))
  const [disallowed, setDisallowed] = useState((initial?.disallowed_tools ?? []).join(', '))
  const [model, setModel] = useState(
    initial?.model && initial.model !== 'inherit' ? initial.model : '',
  )
  const [maxTurns, setMaxTurns] = useState(String(initial?.max_turns ?? 50))
  const [perm, setPerm] = useState(initial?.permission_mode ?? 'default')
  const [background, setBackground] = useState(initial?.background ?? false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const nameValid = /^[A-Za-z][A-Za-z0-9_-]{1,63}$/.test(name.trim())
  const canSave = nameValid && whenToUse.trim() !== '' && systemPrompt.trim() !== ''

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await api.saveAgent(scope, {
        agent_type: name.trim(),
        when_to_use: whenToUse.trim(),
        system_prompt: systemPrompt.trim(),
        tools: tools.split(',').map((s) => s.trim()).filter(Boolean),
        disallowed_tools: disallowed.split(',').map((s) => s.trim()).filter(Boolean),
        model: model.trim(),
        max_turns: Number(maxTurns) || 50,
        permission_mode: perm,
        background,
      })
      onSaved(`已写入 ${res.path},重启 ArchCode 后生效`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!initial) return
    if (!window.confirm(`删除子 agent 定义 "${initial.agent_type}"?`)) return
    setBusy(true)
    setError('')
    try {
      await api.deleteAgent(scope, initial.agent_type)
      onSaved(`已删除 ${initial.agent_type},重启 ArchCode 后生效`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  return (
    <div>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        ‹ 返回列表
      </button>
      <div className={styles.detailTitle}>{isNew ? '新建子 Agent' : `编辑 · ${initial!.agent_type}`}</div>
      <div className={styles.formCard}>
        <div className={styles.formScope}>作用域: {scope === 'project' ? '项目' : '用户'}</div>
        <div className={styles.fieldRow}>
          <div>
            <label className={styles.fieldLabel}>名称</label>
            <input
              className={styles.input}
              placeholder="如 code-reviewer(字母开头)"
              value={name}
              disabled={!isNew}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <label className={styles.fieldLabel}>轮次预算</label>
            <input
              className={styles.input}
              type="number"
              min={1}
              max={500}
              value={maxTurns}
              onChange={(e) => setMaxTurns(e.target.value)}
            />
          </div>
          <div>
            <label className={styles.fieldLabel}>权限模式</label>
            <select className={styles.input} value={perm} onChange={(e) => setPerm(e.target.value)}>
              <option value="default">default</option>
              <option value="acceptEdits">acceptEdits</option>
              <option value="dontAsk">dontAsk</option>
            </select>
          </div>
        </div>
        <label className={styles.fieldLabel}>描述(选用依据,展示给主模型)</label>
        <input
          className={styles.input}
          placeholder="何时应该使用这个子 agent"
          value={whenToUse}
          onChange={(e) => setWhenToUse(e.target.value)}
        />
        <label className={styles.fieldLabel}>模型</label>
        <input
          className={styles.input}
          placeholder="留空沿用主对话模型"
          value={model}
          onChange={(e) => setModel(e.target.value)}
        />
        <div className={styles.fieldRow2}>
          <div>
            <label className={styles.fieldLabel}>工具白名单</label>
            <input
              className={styles.input}
              placeholder="逗号分隔,留空 = 全部工具"
              value={tools}
              onChange={(e) => setTools(e.target.value)}
            />
          </div>
          <div>
            <label className={styles.fieldLabel}>禁用工具</label>
            <input
              className={styles.input}
              placeholder="逗号分隔,可留空"
              value={disallowed}
              onChange={(e) => setDisallowed(e.target.value)}
            />
          </div>
        </div>
        <label className={styles.fieldLabel}>运行方式</label>
        <select
          className={styles.input}
          value={background ? 'bg' : 'fg'}
          onChange={(e) => setBackground(e.target.value === 'bg')}
        >
          <option value="fg">前台(阻塞主对话)</option>
          <option value="bg">后台运行</option>
        </select>
        <label className={styles.fieldLabel}>系统提示词(该子 agent 的身份与规则)</label>
        <textarea
          className={styles.input}
          rows={8}
          placeholder="描述这个子 agent 的角色、边界和规则..."
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
        />
        {error && <div className={styles.errorText}>{error}</div>}
        <div className={styles.editActions}>
          {!isNew && (
            <button type="button" className={styles.deleteBtn} disabled={busy} onClick={remove}>
              删除
            </button>
          )}
          <span className={styles.spring} />
          <button type="button" className={styles.cancelBtn} onClick={onBack}>
            取消
          </button>
          <button
            type="button"
            className={styles.addBtn}
            disabled={!canSave || busy}
            onClick={save}
          >
            保存
          </button>
        </div>
      </div>
      <div className={styles.hint}>
        保存写入当前作用域的 `.archcode/agents/&lt;名称&gt;.md`;与内置定义同名会遮蔽内置版本。
      </div>
    </div>
  )
}

/** hook 常量(与 archcode/hooks/models.py 的合法值同步)。 */
const HOOK_EVENTS: Array<[string, string]> = [
  ['session_start', '会话开始'],
  ['turn_start', '轮次开始'],
  ['pre_tool_use', '工具执行前'],
  ['permission_request', '权限询问时'],
  ['post_tool_use', '工具执行后'],
  ['post_tool_use_failure', '工具执行失败'],
  ['turn_end', '轮次结束'],
  ['session_end', '会话结束'],
]
const HOOK_GATE_EVENTS = new Set(['pre_tool_use', 'permission_request'])
const HOOK_EXECUTORS: Array<[string, string]> = [
  ['command', '命令(本地进程)'],
  ['prompt', '注入提示'],
  ['http', 'HTTP 请求'],
  ['agent', '子 agent'],
]
const HOOK_MAIN_FIELD: Record<string, string> = {
  command: 'command',
  prompt: 'message',
  http: 'url',
  agent: 'prompt',
}

/** 新建/编辑 hook:保存走 settings PUT(config.yaml hooks 键,重启生效)。 */
function HookEditForm({
  hooks,
  editing,
  scope,
  onSave,
  onBack,
}: {
  hooks: Array<Record<string, unknown>>
  editing: number | 'new'
  scope: string
  onSave: (items: Array<Record<string, unknown>>) => void
  onBack: () => void
}) {
  const isNew = editing === 'new'
  const orig: Record<string, unknown> = isNew ? {} : (hooks[editing as number] ?? {})
  const origAction = (orig['action'] as Record<string, unknown>) ?? {}
  const [event, setEvent] = useState(String(orig['event'] ?? 'pre_tool_use'))
  const [actionType, setActionType] = useState(String(origAction['type'] ?? 'command'))
  const [mainValue, setMainValue] = useState(
    String(origAction[HOOK_MAIN_FIELD[String(origAction['type'] ?? 'command')] ?? 'command'] ?? ''),
  )
  const [condition, setCondition] = useState(String(orig['if'] ?? ''))
  const [reject, setReject] = useState(Boolean(orig['reject']))
  const [once, setOnce] = useState(Boolean(orig['once']))
  const [asyncExec, setAsyncExec] = useState(Boolean(orig['async']))
  const [timeout, setTimeoutSec] = useState(String(origAction['timeout'] ?? 60))
  const [error, setError] = useState('')
  const gate = HOOK_GATE_EVENTS.has(event)
  const mainField = HOOK_MAIN_FIELD[actionType]
  const canSave = mainValue.trim() !== ''

  const switchEvent = (next: string) => {
    setEvent(next)
    if (!HOOK_GATE_EVENTS.has(next)) setReject(false)
    if (HOOK_GATE_EVENTS.has(next) || actionType === 'prompt') setAsyncExec(false)
  }
  const switchActionType = (next: string) => {
    setActionType(next)
    setMainValue(String(origAction[HOOK_MAIN_FIELD[next]] ?? ''))
    if (next === 'prompt') setAsyncExec(false)
  }

  const save = () => {
    if (!canSave) return
    const action: Record<string, unknown> = {
      ...origAction,
      type: actionType,
      [mainField]: mainValue.trim(),
      timeout: parseInt(timeout, 10) || 60,
    }
    const entry: Record<string, unknown> = { ...orig, event, action }
    if (isNew) delete entry['id']
    if (condition.trim()) entry['if'] = condition.trim()
    else delete entry['if']
    if (reject) entry['reject'] = true
    else delete entry['reject']
    if (once) entry['once'] = true
    else delete entry['once']
    if (asyncExec) entry['async'] = true
    else delete entry['async']
    delete entry['__source__']
    const items = isNew
      ? [...hooks, entry]
      : hooks.map((h, j) => (j === editing ? entry : h))
    onSave(items)
  }

  return (
    <div>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        ‹ 返回列表
      </button>
      <div className={styles.detailTitle}>{isNew ? '新建钩子' : `编辑 · ${String(orig['id'] ?? event)}`}</div>
      <div className={styles.formCard}>
        <div className={styles.formScope}>作用域: {scope === 'project' ? '项目' : '用户'}</div>
        <div className={styles.fieldRow}>
          <div>
            <label className={styles.fieldLabel}>事件</label>
            <select className={styles.input} value={event} onChange={(e) => switchEvent(e.target.value)}>
              {HOOK_EVENTS.map(([v, label]) => (
                <option key={v} value={v}>
                  {v}({label})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={styles.fieldLabel}>执行方式</label>
            <select
              className={styles.input}
              value={actionType}
              onChange={(e) => switchActionType(e.target.value)}
            >
              {HOOK_EXECUTORS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <label className={styles.fieldLabel}>
          {actionType === 'command'
            ? '命令'
            : actionType === 'http'
              ? 'URL'
              : actionType === 'prompt'
                ? '注入的提示内容'
                : '子 agent 任务提示词'}
        </label>
        {actionType === 'prompt' || actionType === 'agent' ? (
          <textarea
            className={styles.input}
            rows={3}
            value={mainValue}
            onChange={(e) => setMainValue(e.target.value)}
          />
        ) : (
          <input
            className={styles.input}
            placeholder={actionType === 'command' ? "例如 uv run .archcode/hooks/check.py" : '例如 http://127.0.0.1:9000/hook'}
            value={mainValue}
            onChange={(e) => setMainValue(e.target.value)}
          />
        )}
        <label className={styles.fieldLabel}>条件(if 表达式,可选)</label>
        <input
          className={styles.input}
          placeholder={`例如 tool_name == 'Bash',留空 = 总是触发`}
          value={condition}
          onChange={(e) => setCondition(e.target.value)}
        />
        <div className={styles.toggleRow}>
          <span className={styles.toggleLabel}>拒绝执行(reject,仅 {[...HOOK_GATE_EVENTS].join(' / ')})</span>
          <input
            type="checkbox"
            checked={reject}
            disabled={!gate}
            onChange={(e) => setReject(e.target.checked)}
          />
        </div>
        <div className={styles.toggleRow}>
          <span className={styles.toggleLabel}>仅触发一次(once)</span>
          <input type="checkbox" checked={once} onChange={(e) => setOnce(e.target.checked)} />
        </div>
        <div className={styles.toggleRow}>
          <span className={styles.toggleLabel}>异步执行(async,gate 事件与注入提示不可用)</span>
          <input
            type="checkbox"
            checked={asyncExec}
            disabled={gate || actionType === 'prompt'}
            onChange={(e) => setAsyncExec(e.target.checked)}
          />
        </div>
        <div className={styles.fieldRow}>
          <div>
            <label className={styles.fieldLabel}>超时(秒)</label>
            <input
              className={styles.input}
              type="number"
              min={1}
              value={timeout}
              onChange={(e) => setTimeoutSec(e.target.value)}
            />
          </div>
        </div>
        {error && <div className={styles.errorText}>{error}</div>}
        <div className={styles.editActions}>
          <span className={styles.spring} />
          <button type="button" className={styles.cancelBtn} onClick={onBack}>
            取消
          </button>
          <button
            type="button"
            className={styles.addBtn}
            disabled={!canSave}
            onClick={save}
          >
            保存
          </button>
        </div>
      </div>
      <div className={styles.hint}>
        保存写入当前作用域 config.yaml 的 hooks 列表(仅本作用域条目,不合并其他层);重启 ArchCode
        后生效。
      </div>
    </div>
  )
}
