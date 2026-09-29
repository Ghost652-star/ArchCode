import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Item } from '../types'
import { fmtTokens } from '../format'
import CodeBlock from './CodeBlock'
import styles from './ChatItems.module.css'

/** 最近一个已完成段落的首行(流式摘要,DSH ReasoningRow 同款逻辑)。 */
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

export function ChatItems({ items }: { items: Item[] }) {
  return (
    <>
      {items.map((item, index) => (
        <ItemView key={index} item={item} />
      ))}
    </>
  )
}

function ItemView({ item }: { item: Item }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className={styles.userRow}>
          <div className={styles.bubble}>{item.text}</div>
          {item.ts ? <div className={styles.userTime}>{fmtTime(item.ts)}</div> : null}
        </div>
      )
    case 'turnEnd': {
      const parts = [`本轮完成`, `${item.steps} 步`, fmtElapsed(item.elapsed)]
      if (item.tokens && item.tokens > 0) parts.push(`${fmtTokens(item.tokens)} tok`)
      return (
        <div className={styles.turnDivider}>
          <span className={styles.turnLine} />
          <span className={styles.turnText}>{parts.join(' · ')}</span>
          <span className={styles.turnLine} />
        </div>
      )
    }
    case 'notice':
      return <NoticeRow text={item.text} />
    case 'compact':
      return <CompactCard item={item} />
    case 'reasoning':
      return <ReasoningRow text={item.text} running={item.running} />
    case 'tool':
      return <ToolCallRow item={item} />
    case 'assistant':
      return (
        <div className={styles.assistant}>
          <Markdown text={item.text} />
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
function ReasoningRow({ text, running }: { text: string; running: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const summaryText = running ? latestCompletedParagraphFirstLine(text) : firstLine(text)
  const summary = summaryText.replaceAll('**', '')
  const showSummary = summary !== '' && (running || expanded === false)
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
        <span className={styles.reasoningTitle}>Thinking</span>
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

function ToolCallRow({
  item,
}: {
  item: Extract<Item, { kind: 'tool' }>
}) {
  const [expanded, setExpanded] = useState(false)
  const argsSummary = Object.entries(item.args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('  ')
  return (
    <div className={styles.toolRoot}>
      <button
        type="button"
        className={styles.toolRow}
        data-running={item.running || undefined}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className={styles.toolName}>{item.toolName}</span>
        {item.running && <span className={styles.toolSpinner} />}
        <span className={styles.toolArgs}>{argsSummary}</span>
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
          {item.output && <pre className={styles.toolPre}>{item.output}</pre>}
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
