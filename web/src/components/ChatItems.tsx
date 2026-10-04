import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Item } from '../types'
import { fmtTokens } from '../format'
import CodeBlock from './CodeBlock'
import DiffBlock, { extractDiff } from './DiffBlock'
import styles from './ChatItems.module.css'

/** 最近一个已完成段落的首行(流式摘要:答案未完成时取最后一段开头做预览)。 */
function latestCompletedParagraphFirstLine(text: string): string {
  let summary = ''
  let paragraphStart = 0
  const separator = /\r?\n(?:[\t ]*\r?\n)+/g
  for (;;) {
    const nextParagraph = separator.exec(text)
    const paragraphEnd =
      nextParagraph === null ? text.length : nextParagraph.index + nextParagraph[0].indexOf('\n')
    const newline = text.indexOf('\n', paragraphStart)
    if (newline !== -1 && newline <= paragraphEnd) {
      const candidate = text.slice(paragraphStart, newline).trim()
      if (candidate !== '') summary = candidate
    }
    if (nextParagraph === null) return summary
    paragraphStart = nextParagraph.index + nextParagraph[0].length
  }
}

function firstLine(text: string): string {
  const newline = text.indexOf('\n')
  return newline === -1 ? text : text.slice(0, newline)
}

/** epoch ms → HH:MM(用户气泡时间,§13-A1)。 */
function fmtTime(ts?: number): string {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** 轮次用时:秒 / 分秒(§13-A2)。 */
function fmtElapsed(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}分${s % 60}秒`
}

export function ChatItems({ items, runStart }: { items: Item[]; runStart?: number }) {
  return (
    <>
      {groupTurns(items).map((block, index) => (
        <BlockView key={index} block={block} runStart={runStart} />
      ))}
    </>
  )
}

/** ── 轮次分组(§过程流容器):user/notice/modelSwitch/error 独立成块,
 *  其余(思考/工具/assistant/压缩)归入轮次组,turnEnd 闭合并供出用时。 ── */
type Block =
  | { kind: 'single'; item: Item }
  | {
      kind: 'turn'
      running: boolean
      closed: boolean
      elapsed?: number
      steps?: number
      tokens?: number
      body: Item[]
      final?: Extract<Item, { kind: 'assistant' }>
    }

function groupTurns(items: Item[]): Block[] {
  const blocks: Block[] = []
  let cur: Extract<Block, { kind: 'turn' }> | null = null
  const flush = () => {
    if (cur && cur.body.length + (cur.final ? 1 : 0) > 0) blocks.push(cur)
    cur = null
  }
  for (const item of items) {
    if (
      item.kind === 'user' ||
      item.kind === 'notice' ||
      item.kind === 'modelSwitch' ||
      item.kind === 'error'
    ) {
      flush()
      blocks.push({ kind: 'single', item })
      continue
    }
    if (item.kind === 'turnEnd') {
      if (cur) {
        cur.closed = true
        cur.running = false
        cur.elapsed = item.elapsed
        cur.steps = item.steps
        cur.tokens = item.tokens
      }
      flush()
      continue
    }
    if (cur === null) {
      cur = { kind: 'turn', running: false, closed: false, body: [] }
    }
    if (item.kind === 'assistant') {
      // assistant 先挂 final;后续再来 assistant(多段文本)时把旧的挪回 body
      if (cur.final) cur.body.push(cur.final)
      cur.final = item
    } else {
      cur.body.push(item)
    }
  }
  flush()
  // running 判定:组内任何条目仍在流式 → 整组实时展开
  for (const b of blocks) {
    if (b.kind !== 'turn') continue
    b.running = b.body.some((i) => i.kind === 'reasoning' || i.kind === 'tool' ? i.running : false) ||
      (b.final?.running ?? false)
  }
  return blocks
}

/** 轮次内的文件变更统计(编辑/写入类工具,从 output 的 diff 统计增删行)。 */
function turnFileStats(body: Item[]): { files: number; added: number; removed: number } {
  let added = 0
  let removed = 0
  const files = new Set<string>()
  for (const item of body) {
    if (item.kind !== 'tool' || item.isError) continue
    if (item.toolName !== 'EditFile' && item.toolName !== 'WriteFile') continue
    const path = typeof item.args['file_path'] === 'string' ? item.args['file_path'] : ''
    if (path) files.add(path)
    const { diff } = extractDiff(item.output)
    if (!diff) continue
    for (const line of diff.split('\n')) {
      if (line.startsWith('+') && !line.startsWith('+++')) added += 1
      else if (line.startsWith('-') && !line.startsWith('---')) removed += 1
    }
  }
  return { files: files.size, added, removed }
}

function BlockView({ block, runStart }: { block: Block; runStart?: number }) {
  if (block.kind === 'single') return <ItemView item={block.item} />
  return <TurnBlock group={block} runStart={runStart} />
}

/** 轮次容器(参考形态:头部"已工作 Xs"可折叠,过程项挂在左缘竖线上,正文在外)。 */
function TurnBlock({
  group,
  runStart,
}: {
  group: Extract<Block, { kind: 'turn' }>
  runStart?: number
}) {
  const [expanded, setExpanded] = useState(false)
  const showBody = group.running || expanded
  const stats = turnFileStats(group.body)
  const headParts: string[] = []
  if (group.running) {
    headParts.push(runStart ? `已工作 ${fmtElapsed(Date.now() - runStart)}` : '工作中…')
  } else if (group.elapsed) {
    headParts.push(`已工作 ${fmtElapsed(group.elapsed)}`)
  } else {
    headParts.push('已完成')
  }
  const toolCount = group.body.filter((i) => i.kind === 'tool').length
  if (toolCount > 0) headParts.push(`${toolCount} 次工具调用`)
  return (
    <div className={styles.turnRoot} data-running={group.running || undefined}>
      <button
        type="button"
        className={styles.turnHead}
        onClick={() => setExpanded((v) => !v)}
      >
        {group.running && <span className={styles.toolSpinner} />}
        <span className={styles.turnHeadText}>{headParts.join(' · ')}</span>
        {stats.files > 0 && (
          <span className={styles.turnHeadStats}>
            {stats.files} 个文件
            <span className={styles.statAdd}> +{stats.added}</span>
            <span className={styles.statDel}> -{stats.removed}</span>
          </span>
        )}
        <svg
          className={styles.toolChevron}
          width={11}
          height={11}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
          data-open={showBody || undefined}
        >
          <path d="M3 6l5 5 5-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      {showBody && (
        <div className={styles.turnBody}>
          {group.body.map((item, i) => (
            <ItemView key={i} item={item} />
          ))}
        </div>
      )}
      {group.final && (
        <div className={styles.turnFinal}>
          <ItemView item={group.final} />
        </div>
      )}
    </div>
  )
}

function ItemView({ item }: { item: Item }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className={styles.userRow}>
          <div className={styles.userLine}>
            <CopyButton text={item.text} className={styles.userCopy} />
            <div className={styles.bubble}>{renderUserText(item.text)}</div>
          </div>
          {item.ts ? <div className={styles.userTime}>{fmtTime(item.ts)}</div> : null}
        </div>
      )
    case 'turnEnd':
      // 已并入轮次容器头部(groupTurns 消费),防御性兜底
      return null
    case 'modelSwitch':
      return (
        <div className={styles.turnDivider}>
          <span className={styles.turnLine} />
          <span className={styles.turnText}>⇄ {item.text}</span>
          <span className={styles.turnLine} />
        </div>
      )
    case 'notice':
      return <NoticeRow text={item.text} />
    case 'compact':
      return <CompactCard item={item} />
    case 'reasoning':
      return <ReasoningRow text={item.text} running={item.running} elapsed={item.elapsed} />
    case 'tool':
      return <ToolCallRow item={item} />
    case 'assistant':
      return (
        <div className={styles.assistant}>
          <Markdown text={item.text} />
          <CopyButton text={item.text} className={styles.assistantCopy} />
        </div>
      )
    case 'error':
      return (
        <div className={styles.errorRow}>
          <span className={styles.errorDot} />
          <span className={styles.errorText}>{item.message}</span>
        </div>
      )
    default:
      return null
  }
}

/** 思考行:折叠 24px 摘要 + 流式扫描线 + 展开完整 Markdown(§5 规格)。 */
function ReasoningRow({
  text,
  running,
  elapsed,
}: {
  text: string
  running: boolean
  elapsed?: number
}) {
  const [expanded, setExpanded] = useState(false)
  const summaryText = running ? latestCompletedParagraphFirstLine(text) : firstLine(text)
  const summary = summaryText.replaceAll('**', '')
  const showSummary = summary !== '' && (running || expanded === false)
  const elapsedText = !running && elapsed && elapsed > 0 ? fmtElapsed(elapsed) : ''
  return (
    <div
      className={styles.reasoningRoot}
      data-state={running ? 'running' : 'ok'}
      data-expanded={expanded || undefined}
    >
      {running && <span className={styles.visuallyHidden}>思考中</span>}
      <button
        type="button"
        className={styles.reasoningRow}
        data-state={running ? 'running' : 'ok'}
        onClick={() => setExpanded((v) => !v)}
      >
        <svg
          className={styles.reasoningIcon}
          width={14}
          height={14}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
        >
          <path
            d="M8 1.5c3.6 0 6.5 2.6 6.5 5.8 0 2.2-1.3 4.1-3.2 5.1v1.8a.8.8 0 0 1-.8.8H5.5a.8.8 0 0 1-.8-.8v-1.8C2.8 11.4 1.5 9.5 1.5 7.3 1.5 4.1 4.4 1.5 8 1.5z"
            stroke="currentColor"
            strokeWidth="1.2"
          />
        </svg>
        <span className={styles.reasoningTitle}>
          思考{!running && elapsedText ? ` · ${elapsedText}` : ''}
        </span>
        {showSummary && (
          <>
            <span className={styles.separator} aria-hidden />
            <span className={styles.reasoningSummary} data-streaming={running || undefined}>
              <span className={styles.reasoningSummaryText}>{summary}</span>
            </span>
          </>
        )}
        <svg
          className={styles.reasoningChevron}
          width={12}
          height={12}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
          data-open={expanded || undefined}
        >
          <path d="M3 6l5 5 5-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      {expanded && (
        <div className={styles.reasoningBody}>
          <Markdown text={text} />
        </div>
      )}
    </div>
  )
}

/** 提示行:服务端 notice(斜杠命令反馈/自动重试等),居中 caption 弱化展示。 */
function NoticeRow({ text }: { text: string }) {
  return (
    <div className={styles.noticeRow}>
      <span className={styles.noticeLine} />
      <span className={styles.noticeText}>{text}</span>
      <span className={styles.noticeLine} />
    </div>
  )
}

/** 上下文压缩卡:running 进度 → done(可展开摘要预览)/ failed(错误原因)。 */
function CompactCard({ item }: { item: Extract<Item, { kind: 'compact' }> }) {
  const [expanded, setExpanded] = useState(false)
  const hasDetail =
    item.state === 'done' && Boolean(item.summaryPreview) && item.summaryPreview !== ''
  return (
    <div className={styles.compactRoot} data-state={item.state}>
      <button
        type="button"
        className={styles.compactRow}
        onClick={() => hasDetail && setExpanded((v) => !v)}
        disabled={item.state === 'running'}
      >
        {item.state === 'running' ? (
          <span className={styles.toolSpinner} />
        ) : (
          <svg width={13} height={13} viewBox="0 0 16 16" fill="none" aria-hidden>
            {item.state === 'done' ? (
              <path
                d="M3 8.5l3.5 3.5L13 5"
                stroke="var(--ac-state-success)"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            ) : (
              <path
                d="M4 4l8 8M12 4l-8 8"
                stroke="var(--ac-state-error)"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            )}
          </svg>
        )}
        <span className={styles.compactTitle}>
          {item.state === 'running' && '上下文压缩中…'}
          {item.state === 'done' && `上下文压缩完成 · 释放 ${item.dropped ?? 0} 条消息`}
          {item.state === 'failed' && `压缩失败：${item.error || '未知原因'}`}
        </span>
        {item.state === 'running' && item.totalChars > 0 && (
          <span className={styles.compactMeta}>已生成 {item.totalChars} 字符</span>
        )}
        {hasDetail && (
          <svg
            className={styles.toolChevron}
            width={11}
            height={11}
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden
            data-open={expanded || undefined}
          >
            <path d="M3 6l5 5 5-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        )}
      </button>
      {expanded && hasDetail && (
        <div className={styles.compactBody}>
          <Markdown text={item.summaryPreview ?? ''} />
        </div>
      )}
    </div>
  )
}

/** 用户消息里的 @文件引用渲染成 chip(纯展示,消息内容不变)。 */
function renderUserText(text: string) {
  const parts = text.split(/(@[\w\-./\\]+)/g)
  if (parts.length === 1) return text
  return parts.map((part, i) =>
    part.startsWith('@') && part.length > 1 ? (
      <span key={i} className={styles.atChip}>
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

/** 复制按钮:悬停浮现,点击写入剪贴板,短暂显示成功态。 */
function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className={`${styles.copyBtn} ${className ?? ''}`}
      data-copied={copied || undefined}
      title={copied ? '已复制' : '复制'}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        } catch {
          /* 剪贴板不可用(权限/非安全上下文)时静默 */
        }
      }}
    >
      {copied ? (
        <svg width={13} height={13} viewBox="0 0 16 16" fill="none" aria-hidden>
          <path d="M3 8.5l3.5 3.5L13 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      ) : (
        <svg width={13} height={13} viewBox="0 0 16 16" fill="none" aria-hidden>
          <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
          <path d="M10.5 5.5V4a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5" stroke="currentColor" strokeWidth="1.2" fill="none" />
        </svg>
      )}
    </button>
  )
}

/** 工具动词映射:过程行用动作词而非英文工具名(观感对齐参考形态)。 */
const TOOL_VERBS: Record<string, string> = {
  Bash: '终端',
  ReadFile: '读取',
  WriteFile: '写入',
  EditFile: '编辑',
  Grep: '搜索',
  Glob: '查找',
  Agent: '子代理',
  TodoWrite: '任务清单',
  LoadSkill: '技能',
  TaskList: '任务',
  TaskGet: '任务',
}

/** 工具行行首小图标(按类别:读/搜=放大镜,写/编=铅笔,Bash=终端,Agent=机器人)。 */
function toolIcon(name: string): JSX.Element {
  const stroke = 'currentColor'
  const common = { width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': true as const }
  switch (name) {
    case 'ReadFile':
    case 'Grep':
    case 'Glob':
      return (
        <svg {...common}>
          <circle cx="7" cy="7" r="4.5" stroke={stroke} strokeWidth="1.3" />
          <path d="M10.5 10.5L14 14" stroke={stroke} strokeWidth="1.3" strokeLinecap="round" />
        </svg>
      )
    case 'WriteFile':
    case 'EditFile':
      return (
        <svg {...common}>
          <path d="M11.3 2.2l2.5 2.5L5 13.5l-3 .8.8-3 8.5-8.5z" stroke={stroke} strokeWidth="1.2" strokeLinejoin="round" />
        </svg>
      )
    case 'Bash':
      return (
        <svg {...common}>
          <rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.6" stroke={stroke} strokeWidth="1.2" />
          <path d="M4.5 6.5L6.8 8.5L4.5 10.5M8.5 11h3" stroke={stroke} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )
    case 'Agent':
      return (
        <svg {...common}>
          <rect x="3" y="5" width="10" height="8" rx="2" stroke={stroke} strokeWidth="1.2" />
          <path d="M8 5V2.8M5.8 8.6h.01M10.2 8.6h.01M6 10.5h4" stroke={stroke} strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      )
    case 'TodoWrite':
    case 'TaskList':
    case 'TaskGet':
      return (
        <svg {...common}>
          <path d="M3 4.5h10M3 8h10M3 11.5h6" stroke={stroke} strokeWidth="1.3" strokeLinecap="round" />
        </svg>
      )
    default:
      return (
        <svg {...common}>
          <rect x="3" y="3" width="10" height="10" rx="2" stroke={stroke} strokeWidth="1.2" />
        </svg>
      )
  }
}

/** 文件路径双色:文件名亮,目录弱(对齐参考形态)。 */
function filePathParts(raw: string): { dir: string; base: string } {
  const norm = raw.replaceAll('\\', '/')
  const cut = Math.max(norm.lastIndexOf('/'), 0)
  return { dir: norm.slice(0, cut), base: norm.slice(cut + 1) || raw }
}

/** 单工具的增删统计(编辑/写入类,从 output 的 diff 统计;无 diff 返回 null)。 */
function toolDiffStat(item: Extract<Item, { kind: 'tool' }>): { added: number; removed: number } | null {
  if (item.toolName !== 'EditFile' && item.toolName !== 'WriteFile') return null
  const { diff } = extractDiff(item.output)
  if (!diff) return null
  let added = 0
  let removed = 0
  for (const line of diff.split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) added += 1
    else if (line.startsWith('-') && !line.startsWith('---')) removed += 1
  }
  return { added, removed }
}

/** 按工具定制的折叠摘要(bash 显示命令,文件类显示路径,搜索类显示 pattern)。 */
function toolSummary(item: Extract<Item, { kind: 'tool' }>): string {
  const a = item.args
  const s = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v) ?? '')
  switch (item.toolName) {
    case 'Bash':
      return s(a.command)
    case 'ReadFile':
      return s(a.file_path) + (a.offset != null ? ` (offset ${s(a.offset)})` : '')
    case 'WriteFile':
    case 'EditFile':
      return s(a.file_path)
    case 'Grep':
      return s(a.pattern) + (a.path ? ` in ${s(a.path)}` : '')
    case 'Glob':
      return s(a.pattern)
    case 'Agent':
      return s(a.name) || s(a.description) || 'subagent'
    case 'TodoWrite': {
      const todos = Array.isArray(a.todos) ? (a.todos as unknown[]) : []
      return `${todos.length} 项`
    }
    default: {
      const summary = Object.entries(a)
        .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
        .join('  ')
      return summary
    }
  }
}

function ToolCallRow({
  item,
}: {
  item: Extract<Item, { kind: 'tool' }>
}) {
  const [expanded, setExpanded] = useState(false)
  const summary = toolSummary(item)
  // 文件修改类工具:output 自带统一 diff 段,展开时用 DiffBlock 渲染
  const showDiff = item.toolName === 'EditFile' || item.toolName === 'WriteFile'
  const { summary: outputHead, diff } = showDiff
    ? extractDiff(item.output)
    : { summary: item.output, diff: null }
  // 文件类工具:摘要用双色路径(文件名亮/目录弱);动词替代英文工具名
  const pathArg =
    (item.toolName === 'ReadFile' ||
      item.toolName === 'WriteFile' ||
      item.toolName === 'EditFile') &&
    typeof item.args['file_path'] === 'string'
      ? (item.args['file_path'] as string)
      : null
  const stat = toolDiffStat(item)
  const verb = TOOL_VERBS[item.toolName] ?? item.toolName
  return (
    <div className={styles.toolRoot}>
      <button
        type="button"
        className={styles.toolRow}
        data-running={item.running || undefined}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className={styles.toolIcon}>{toolIcon(item.toolName)}</span>
        <span className={styles.toolName}>{verb}</span>
        {item.running && <span className={styles.toolSpinner} />}
        {pathArg !== null ? (
          (() => {
            const { dir, base } = filePathParts(pathArg)
            return (
              <span className={styles.toolArgs}>
                <span className={styles.fileBase}>{base}</span>
                {dir && <span className={styles.fileDir}> {dir}</span>}
              </span>
            )
          })()
        ) : (
          <span className={styles.toolArgs}>{summary}</span>
        )}
        {stat && (stat.added > 0 || stat.removed > 0) && (
          <span className={styles.toolDiffStat}>
            {stat.added > 0 && <span className={styles.statAdd}>+{stat.added}</span>}
            {stat.removed > 0 && <span className={styles.statDel}>-{stat.removed}</span>}
          </span>
        )}
        {item.elapsed !== undefined && item.elapsed > 0 && (
          <span className={styles.toolElapsed}>{item.elapsed.toFixed(1)}s</span>
        )}
        {item.isError && <span className={styles.toolErrorDot} />}
        <svg
          className={styles.toolChevron}
          width={11}
          height={11}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
        >
          <path d="M3 6l5 5 5-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      {expanded && (
        <div className={styles.toolBody}>
          {Object.entries(item.args).length > 0 && (
            <pre className={styles.toolPre}>{JSON.stringify(item.args, null, 2)}</pre>
          )}
          {diff ? (
            <>
              <div className={styles.toolOutputHead}>{outputHead}</div>
              <DiffBlock diff={diff} />
            </>
          ) : (
            item.output && <pre className={styles.toolPre}>{item.output}</pre>
          )}
        </div>
      )}
    </div>
  )
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 块级代码走 Prism 高亮(§10.9-1);pre 解包避免双层容器;行内 code 保持原样
          pre: ({ children }) => <>{children}</>,
          code: (props: { className?: string; children?: React.ReactNode }) => {
            const { className, children } = props
            const match = /language-(\w+)/.exec(className ?? '')
            if (!match) return <code className={className}>{children}</code>
            return (
              <CodeBlock code={String(children)} language={match[1]} />
            )
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
