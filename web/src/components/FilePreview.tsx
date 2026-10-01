import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { FileContent } from '../types'
import { Markdown } from './ChatItems'
import CodeBlock, { langFromFilename } from './CodeBlock'
import styles from './FilePreview.module.css'

interface Props {
  /** 所属工作区(服务端按它路由文件访问)。 */
  workspace: string
  /** 工作区相对路径。 */
  path: string
  /** 外部刷新信号(面板刷新钮):每次值变化重读。 */
  refreshSignal: number
}

/** 文件预览:.md → Markdown;其余 → Prism 高亮 + 行号;二进制/超限 → 提示(设计 §10.9-1/§12.4)。 */
export default function FilePreview({ workspace, path, refreshSignal }: Props) {
  const [content, setContent] = useState<FileContent | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    setContent(null)
    try {
      setContent(await api.readFile(workspace, path))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [workspace, path])

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

  const text = content.text.replace(/\r\n/g, '\n') // Windows CRLF:\r 会渲染成多余换行
  const lineCount = text === '' ? 0 : text.split('\n').length
  return (
    <div className={styles.root}>
      {isMarkdown ? (
        <div className={styles.markdownScroll}>
          <Markdown text={text} />
        </div>
      ) : (
        <div className={styles.codeScroll}>
          <CodeBlock code={text} language={langFromFilename(name)} lineNumbers />
          {lineCount === 0 && <div className={styles.hint}>(空文件)</div>}
        </div>
      )}
      {content.truncated && (
        <div className={styles.truncated}>文件过大,仅显示前 {lineCount} 行</div>
      )}
    </div>
  )
}
