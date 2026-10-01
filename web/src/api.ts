/** 服务端 API 客户端:REST + SSE 订阅(多会话并行版,plan §4.3)。 */

import type {
  AgentState,
  ContextInfo,
  DirListing,
  FileContent,
  FileSearch,
  SessionInfo,
  TodoItem,
  UsageTotal,
} from './types'

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

const q = (params: Record<string, string | number | undefined>) => {
  const usp = new URLSearchParams()
  for (const [k, v] of Object.entries(params))
    if (v !== undefined && v !== '') usp.set(k, String(v))
  const s = usp.toString()
  return s ? `?${s}` : ''
}

export const api = {
  /** 全局状态:运行中会话 + 已注册工作区。 */
  state: () =>
    jsonFetch<{
      running_session_ids: string[]
      workspaces: string[]
      max_concurrent_runs: number
    }>('/api/state'),

  registerWorkspace: (path: string) =>
    jsonFetch<{ ok: boolean; workspaces: string[]; work_dir: string }>('/api/workspaces', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),

  // ── 模型 ──
  model: () =>
    jsonFetch<{
      current: string
      providers: Array<{ name: string; model: string; protocol: string }>
    }>('/api/model'),

  switchModel: (name: string) =>
    jsonFetch<{ ok: boolean; model: string }>('/api/model', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  // ── 会话 ──
  sessionsByWorkspace: (workspace: string) =>
    jsonFetch<{ workspace: string; sessions: SessionInfo[] }>(
      `/api/sessions${q({ workspace })}`,
    ).then((r) => r.sessions),

  newSession: (workspace: string) =>
    jsonFetch<{ session_id: string; workspace: string }>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ workspace }),
    }),

  resume: (sessionId: string, workspace: string) =>
    jsonFetch<{ ok: boolean }>(
      `/api/sessions/${sessionId}/resume${q({ workspace })}`,
      { method: 'POST' },
    ),

  renameSession: (sessionId: string, workspace: string, title: string) =>
    jsonFetch<{ ok: boolean }>(
      `/api/sessions/${sessionId}/rename${q({ workspace })}`,
      { method: 'POST', body: JSON.stringify({ title }) },
    ),

  deleteSession: (sessionId: string, workspace: string) =>
    jsonFetch<{ ok: boolean }>(`/api/sessions/${sessionId}${q({ workspace })}`, {
      method: 'DELETE',
    }),

  sessionsSearch: (workspace: string, query: string) =>
    jsonFetch<{ results: Array<{ id: string; title: string; excerpt: string }> }>(
      `/api/sessions/search${q({ workspace, q: query })}`,
    ),

  history: (sessionId: string) =>
    jsonFetch<Array<Record<string, unknown>>>(
      `/api/history${q({ session_id: sessionId })}`,
    ),

  // ── 对话运行 ──
  chat: (sessionId: string, text: string) =>
    jsonFetch<{ ok: boolean; session_id: string }>('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, text }),
    }),

  /** 订阅会话事件流的 SSE 地址(after = 已消费到的 seq,断线续传)。 */
  eventsUrl: (sessionId: string, after: number = -1) =>
    `/api/events/${sessionId}${q({ after })}`,

  abort: (sessionId: string) =>
    jsonFetch<{ ok: boolean }>('/api/abort', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    }),

  // ── 逐会话观测 ──
  context: (sessionId: string) =>
    jsonFetch<ContextInfo>(`/api/context${q({ session_id: sessionId })}`),

  usage: (sessionId: string) =>
    jsonFetch<UsageTotal>(`/api/usage${q({ session_id: sessionId })}`),

  todo: (sessionId: string) => jsonFetch<{ todos: TodoItem[] }>(`/api/todo${q({ session_id: sessionId })}`),

  tasks: (sessionId: string) =>
    jsonFetch<{
      tasks: Array<{
        id: string
        name: string
        status: string
        elapsed: number
        input_tokens: number
        output_tokens: number
        result_preview: string
      }>
    }>(`/api/tasks${q({ session_id: sessionId })}`),

  // ── 权限 ──
  answerPermission: (requestId: string, body: { allowed?: boolean; answer?: string }) =>
    jsonFetch<{ ok: boolean }>(`/api/permission/${requestId}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  permissionMode: (sessionId: string) =>
    jsonFetch<{ mode: string; plan_mode: boolean }>(
      `/api/permission-mode${q({ session_id: sessionId })}`,
    ),

  setPermissionMode: (sessionId: string, mode: string) =>
    jsonFetch<{ ok: boolean; mode: string }>('/api/permission-mode', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, mode }),
    }),

  // ── Skills / 子 Agent(按工作区)──
  skills: (workspace: string) =>
    jsonFetch<
      Array<{ name: string; description: string; source: string; path: string; is_directory: boolean }>
    >(`/api/skills${q({ workspace })}`),

  agents: (workspace: string) =>
    jsonFetch<
      Array<{
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
      }>
    >(`/api/agents${q({ workspace })}`),

  saveAgent: (
    workspace: string,
    scope: string,
    payload: {
      agent_type: string
      when_to_use: string
      system_prompt: string
      tools: string[]
      disallowed_tools: string[]
      model: string
      max_turns: number
      permission_mode: string
      background: boolean
    },
  ) =>
    jsonFetch<{ ok: boolean; path: string; restart_required: boolean }>(
      `/api/agents/${scope}${q({ workspace })}`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  deleteAgent: (workspace: string, scope: string, agentType: string) =>
    jsonFetch<{ ok: boolean }>(
      `/api/agents/${scope}/${agentType}${q({ workspace })}`,
      { method: 'DELETE' },
    ),

  // ── 设置(config.yaml 读写,按工作区)──
  getSettings: (scope: string, workspace: string) =>
    jsonFetch<{ path: string; data: Record<string, unknown>; exists: boolean }>(
      `/api/settings/${scope}${q({ workspace })}`,
    ),

  putSettings: (scope: string, workspace: string, key: string, items: unknown[]) =>
    jsonFetch<{ ok: boolean; restart_required: boolean }>(
      `/api/settings/${scope}${q({ workspace })}`,
      { method: 'PUT', body: JSON.stringify({ key, items }) },
    ),

  // ── 工作区文件(按工作区)──
  listFiles: (workspace: string, path: string) =>
    jsonFetch<DirListing>(`/api/files${q({ workspace, path })}`),

  readFile: (workspace: string, path: string) =>
    jsonFetch<FileContent>(`/api/file${q({ workspace, path })}`),

  filesSearch: (workspace: string, query: string) =>
    jsonFetch<FileSearch>(`/api/files/search${q({ workspace, q: query })}`),

  pickDirectory: () =>
    jsonFetch<{ ok: boolean; path: string | null }>('/api/workspace/pick', {
      method: 'POST',
    }),
}

/**
 * 订阅会话事件流(EventSource)。返回关闭函数;
 * 连接意外断开时浏览器自动用 Last-Event-ID 续传。
 */
export function subscribeEvents(
  sessionId: string,
  onEvent: (event: { type: string; [k: string]: unknown }) => void,
  onEnd: () => void,
): () => void {
  const es = new EventSource(`/api/events/${sessionId}`)
  es.addEventListener('agent', (e) => {
    try {
      const event = JSON.parse((e as MessageEvent).data)
      if (event.type === 'done') {
        es.close()
        onEnd()
        return
      }
      onEvent(event)
    } catch {
      /* 忽略解析失败的分片 */
    }
  })
  es.onerror = () => {
    // 正常结束走 done 哨兵(上方主动 close);这里兜底网络级断亡。
    // 注意:服务端流正常结束时浏览器是自动重连(readyState=CONNECTING),不是 CLOSED——
    // 判断 CLOSED 才算结束只对"连接彻底死亡"成立,不能作为流的常规结束信号。
    if (es.readyState === EventSource.CLOSED) onEnd()
  }
  return () => es.close()
}