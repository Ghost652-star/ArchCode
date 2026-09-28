import { useCallback, useState } from 'react'
import FileTree from './FileTree'
import FilePreview from './FilePreview'
import styles from './FilePanel.module.css'

interface Props {
  /** 服务端绑定工作区(树根)。 */
  workDir: string
  showBrowsingHint: boolean
}

type Tab = { kind: 'tree' } | { kind: 'file'; path: string }

/** 右栏外壳:tab 条(固定"文件" + 可关文件 tab)+ 内容区(设计 §12.4)。 */
export default function FilePanel({ workDir, showBrowsingHint }: Props) {
  const [tabs, setTabs] = useState<Tab[]>([{ kind: 'tree' }])
  const [active, setActive] = useState<string>('tree') // 'tree' | 文件相对路径
  const [refreshSignal, setRefreshSignal] = useState(0)

  const openFile = useCallback((path: string) => {
    setTabs((prev) => (prev.some((t) => t.kind === 'file' && t.path === path) ? prev : [...prev, { kind: 'file', path }]))
    setActive(path)
  }, [])

  const closeTab = useCallback(
    (path: string, e: React.MouseEvent) => {
      e.stopPropagation()
      setTabs((prev) => {
        const next = prev.filter((t) => !(t.kind === 'file' && t.path === path))
        return next
      })
      setActive((cur) => {
        if (cur !== path) return cur
        // 关掉当前 tab → 回到"文件"树(不做 DSH 的邻位聚焦,单窗格够用)
        return 'tree'
      })
    },
    [],
  )

  const refresh = useCallback(() => setRefreshSignal((n) => n + 1), [])

  const activeFile = tabs.find(
    (t): t is Extract<Tab, { kind: 'file' }> => t.kind === 'file' && t.path === active,
  )

  return (
    <div className={styles.root}>
      <div className={styles.strip}>
        <button
          className={`${styles.chip} ${active === 'tree' ? styles.chipActive : ''}`}
          onClick={() => setActive('tree')}
          title={workDir}
        >
          <svg width={14} height={14} viewBox="0 0 16 16" fill="none" aria-hidden>
            <path
              d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 2h4.5A1.5 1.5 0 0 1 14 6.5v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7z"
              stroke="currentColor"
              strokeWidth="1.2"
            />
          </svg>
          <span>文件</span>
        </button>
        {tabs.map((t) =>
          t.kind === 'file' ? (
            <button
              key={t.path}
              className={`${styles.chip} ${active === t.path ? styles.chipActive : ''}`}
              onClick={() => setActive(t.path)}
              title={t.path}
            >
              <span className={styles.chipName}>{t.path.split('/').pop()}</span>
              <span
                className={styles.chipClose}
                role="button"
                aria-label="关闭"
                onClick={(e) => closeTab(t.path, e)}
              >
                ×
              </span>
            </button>
          ) : null,
        )}
        <span className={styles.stripSpring} />
        <button className={styles.iconBtn} onClick={refresh} title="刷新">
          <svg width={15} height={15} viewBox="0 0 16 16" fill="none" aria-hidden>
            <path
              d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v2.6h-2.6"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
      <div className={styles.body}>
        {activeFile ? (
          <FilePreview path={activeFile.path} refreshSignal={refreshSignal} />
        ) : (
          <FileTree
            workDir={workDir}
            showBrowsingHint={showBrowsingHint}
            onOpenFile={openFile}
            refreshSignal={refreshSignal}
          />
        )}
      </div>
    </div>
  )
}
