/** 服务端 API 客户端:REST + SSE 流解析(plan §4.1/§4.2)。 */

import type { AgentState, SessionInfo, WireEvent } from './types'

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

export const api = {
  state: () => jsonFetch<AgentState>('/api/state'),

  sessions: () => jsonFetch<SessionInfo[]>('/api/sessions'),

  newSession: () => jsonFetch<{ session_id: string }>('/api/sessions', { method: 'POST' }),

  resumeSession: (id: string) =>
    jsonFetch<{ session_id: string }>(`/api/sessions/${id}/resume`, { method: 'POST' }),

  history: () => jsonFetch<Array<Record<string, unknown>>>('/api/history'),

  abort: () => jsonFetch<{ ok: boolean }>('/api/abort', { method: 'POST' }),

  context: () => jsonFetch<{ total_tokens: number; percent: number }>('/api/context'),

  skills: () =>
    jsonFetch<
      Array<{ name: string; description: string; source: string; path: string; is_directory: boolean }>
    >('/api/skills'),

  answerPermission: (requestId: string, body: { allowed?: boolean; answer?: string }) =>
    jsonFetch<{ ok: boolean }>(`/api/permission/${requestId}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  setPermissionMode: (mode: string) =>
    jsonFetch<{ ok: boolean }>(`/api/permission-mode`, {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),

  getSettings: (scope: string) =>
    jsonFetch<{ path: string; data: Record<string, unknown>; exists: boolean }>(
      `/api/settings/${scope}`,
    ),

  putSettings: (scope: string, key: string, items: unknown[]) =>
    jsonFetch<{ ok: boolean; restart_required: boolean }>(`/api/settings/${scope}`, {
      method: 'PUT',
      body: JSON.stringify({ key, items }),
    }),
}

/**
 * POST /api/chat 并解析 SSE 流。每个 agent 事件回调一次;
 * 服务端断开 / 流结束(done)时 resolve。abort 由调用方触发独立端点。
 */
export async function streamChat(
  text: string,
  onEvent: (event: WireEvent) => void,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${detail}`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index: number
    while ((index = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      for (const line of frame.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6)) as WireEvent
            if (event.type === 'done') return
            onEvent(event)
          } catch {
            // 忽略解析失败的分片
          }
        }
      }
    }
  }
}
