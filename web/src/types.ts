/** wire 协议类型(plan §4.2)与服务端数据形状。 */

export interface WireEvent {
  type: string
  [key: string]: unknown
}

export interface SessionInfo {
  id: string
  created?: string
  current?: boolean
  running?: boolean
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
  | { kind: 'user'; text: string }
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
