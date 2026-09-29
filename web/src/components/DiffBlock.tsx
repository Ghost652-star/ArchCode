import styles from './DiffBlock.module.css'

type DiffLineType = 'meta' | 'hunk' | 'add' | 'del' | 'ctx'

interface DiffLine {
  type: DiffLineType
  text: string
}

/** 解析统一 diff 文本(difflib.unified_diff 产物)。 */
function parseDiff(text: string): DiffLine[] {
  return text
    .replace(/\n$/, '')
    .split('\n')
    .map((line) => {
      if (line.startsWith('--- before') || line.startsWith('+++ after')) {
        return { type: 'meta' as const, text: line }
      }
      if (line.startsWith('@@')) return { type: 'hunk' as const, text: line }
      if (line.startsWith('+')) return { type: 'add' as const, text: line }
      if (line.startsWith('-')) return { type: 'del' as const, text: line }
      return { type: 'ctx' as const, text: line }
    })
}

/** 从工具 output 里截取 diff 段(第一行是摘要,之后是 \n\n--- before 开头的 diff)。 */
export function extractDiff(output: string): { summary: string; diff: string | null } {
  const newline = output.indexOf('\n')
  const summary = newline === -1 ? output : output.slice(0, newline)
  const marker = '\n--- before\n'
  const idx = output.indexOf(marker)
  if (idx >= 0) return { summary, diff: output.slice(idx + 1) }
  if (output.startsWith('--- before\n')) return { summary, diff: output }
  return { summary, diff: null }
}

/** 统一 diff 渲染:增删行着色,hunk 头弱化(轻量版,不引入语法高亮)。 */
export default function DiffBlock({ diff }: { diff: string }) {
  const lines = parseDiff(diff)
  return (
    <div className={styles.root}>
      {lines.map((line, i) => (
        <div key={i} className={styles.line} data-type={line.type}>
          <span className={styles.sign} aria-hidden>
            {line.type === 'add' ? '+' : line.type === 'del' ? '-' : ''}
          </span>
          <span className={styles.text}>{line.text}</span>
        </div>
      ))}
    </div>
  )
}
