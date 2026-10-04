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
  plan_mode?: boolean
}

/** 对话流条目(SSE 事件累积的渲染单元)。 */
export type Item =
  | { kind: 'user'; text: string; ts?: number }
  | { kind: 'reasoning'; text: string; running: boolean; startTs?: number; elapsed?: number }
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
  | { kind: 'assistant'; text: string; running: boolean; ts?: number }
  | { kind: 'turnEnd'; steps: number; elapsed: number; tokens?: number }
  /** 模型切换分隔条(持久化在会话里的 <model-switch> 通知)。 */
  | { kind: 'modelSwitch'; text: string }
  | { kind: 'error'; message: string }
  /** 服务端提示行(/plan 切换、斜杠命令不支持、请求重试等)。 */
  | { kind: 'notice'; text: string }
  /** 上下文压缩进度卡(running → done/failed)。 */
  | {
      kind: 'compact'
      state: 'running' | 'done' | 'failed'
      mode: string
      totalChars: number
      dropped?: number
      summaryPreview?: string
      error?: string
    }

export interface QuestionOption {
  label: string
  description?: string
}

export interface PermissionState {
  requestId: string
  toolName: string
  reason: string
  question: string | null
  /** AskUserQuestion 的选项可能是字符串或 {label, description} 对象。 */
  options: Array<string | QuestionOption> | null
  multiSelect: boolean
}

export interface Usage {
  inputTokens: number
  outputTokens: number
  cacheRead: number
  cacheCreation: number
}

/** 会话累计用量(GET /api/usage;SSE usage 事件在前端增量累加)。 */
export interface UsageTotal {
  input_tokens: number
  output_tokens: number
  cache_read: number
  cache_creation: number
  llm_rounds: number
  total_tokens: number
  cache_hit: number | null
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

/** 上下文占用(§13-A4):percent 为 0~1;breakdown 为启发式分段估算(token 数)。 */
export interface ContextInfo {
  total_tokens: number
  percent: number
  window: number
  breakdown?: {
    messages: number
    system: number
    tools_builtin: number
    tools_mcp: number
    skills: number
    memory: number
  }
}

/** 会话任务清单(TodoWrite 工具维护,面板可视化)。 */
export interface TodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed' | string
}

/** @ 文件引用搜索结果(§13-B1)。 */
export interface FileSearch {
  results: string[]
}
