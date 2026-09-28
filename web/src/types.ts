/** wire 协议类型(plan §4.2)与服务端数据形状。 */

export interface WireEvent {
  type: string
  [key: string]: unknown
}

export interface SessionInfo {
  id: string
  title?: string
  message_count?: number
  last_active_ms?: number
  created?: string
  current?: boolean
  running?: boolean
  workspace?: string
}

export interface AgentState {
  session_id: string | null
  running: boolean
  work_dir: string
  model: string
  permission_mode: string
}

/** 对话流条目(SSE 事件累积的渲染单元)。 */
export type Item =
  | { kind: 'user'; text: string; ts?: number }
  | { kind: 'reasoning'; text: string; running: boolean }
  | {
      kind: 'tool'
      toolId: string
      toolName: string
      args: Record<string, unknown>
      output: string
      isError: boolean
      running: boolean
      elapsed?: number
    }
  | { kind: 'assistant'; text: string; running: boolean }
  | { kind: 'turnEnd'; steps: number; elapsed: number }
  | { kind: 'error'; message: string }

export interface PermissionState {
  requestId: string
  toolName: string
  reason: string
  question: string | null
  options: string[] | null
  multiSelect: boolean
}

export interface Usage {
  inputTokens: number
  outputTokens: number
  cacheRead: number
  cacheCreation: number
}

/** ── 工作区文件面板(设计 §12)────────────────────────────── */

export interface FileEntry {
  name: string
  type: 'directory' | 'file' | 'other'
  size?: number
}

export interface DirListing {
  path: string
  entries: FileEntry[]
  truncated: boolean
}

export interface FileContent {
  text: string
  truncated: boolean
  size: number
  binary: boolean
}

/** 上下文占用(§13-A4):percent 为 0~1。 */
export interface ContextInfo {
  total_tokens: number
  percent: number
  window: number
}

/** @ 文件引用搜索结果(§13-B1)。 */
export interface FileSearch {
  results: string[]
}
