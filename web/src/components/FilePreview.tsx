import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { FileContent } from '../types'
import { Markdown } from './ChatItems'
import styles from './FilePreview.module.css'

interface Props {
  /** 工作区相对路径。 */
  path: string
  /** 外部刷新信号(面板刷新钮):每次值变化重读。 */
  refreshSignal: number
}

/** 文件预览:.md → Markdown;其余 → 等宽文本 + 行号;二进制/超限 → 提示(设计 §12.4)。 */
export default function FilePreview({ path, refreshSignal }: Props) {
  const [content, setContent] = useState<FileContent | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    setContent(null)
    try {
      setContent(await api.readFile(path))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [path])

  useEffect(() => {
    load()
  }, [load, refreshSignal])

  const name = path.split('/').pop() ?? path
  const isMarkdown = /\.(md|markdown)$/i.test(path)

  if (error) {
    return (
      <div className={styles.root}>
        <div className={styles.hint}>{error}</div>
      </div>
    )
  }
  if (!content) {
    return (
      <div className={styles.root}>
        <div className={styles.hint}>加载中…</div>
      </div>
    )
  }
  if (content.binary) {
    return (
      <div className={styles.root}>
        <div className={styles.hint}>{name} 是二进制文件,暂不支持预览</div>
      </div>
    )
  }

  const lineCount = content.text === '' ? 0 : content.text.split('\n').length
  return (
    <div className={styles.root}>
      {isMarkdown ? (
        <div className={styles.markdownScroll}>
          <Markdown text={content.text} />
        </div>
      ) : (
        <div className={styles.codeScroll}>
          <pre className={styles.code}>
            {content.text
              .replace(/\r\n/g, '\n') // Windows CRLF:行结构由 div 决定,\r 会渲染成多余换行
              .split('\n')
              .map((line, i) => (
                <div key={i} className={styles.codeLine}>
                  <span className={styles.lineNo}>{i + 1}</span>
                  <span className={styles.lineText}>{line || ' '}</span>
                </div>
              ))}
            {lineCount === 0 && <div className={styles.hint}>(空文件)</div>}
          </pre>
        </div>
      )}
      {content.truncated && (
        <div className={styles.truncated}>文件过大,仅显示前 {lineCount} 行</div>
      )}
    </div>
  )
}
