import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { FileEntry } from '../types'
import styles from './FileTree.module.css'

interface Props {
  /** 服务端绑定工作区(树根,完整路径展示用)。 */
  workDir: string
  /** 非服务端工作区激活时的提示(树仍浏览服务端目录)。 */
  showBrowsingHint: boolean
  onOpenFile: (path: string) => void
  /** 外部刷新信号(面板刷新钮):每次值变化重列根 + 已展开层。 */
  refreshSignal: number
}

type LevelState =
  | { status: 'loading' }
  | { status: 'ready'; entries: FileEntry[]; truncated: boolean }
  | { status: 'failed'; message: string }

/** 工作区文件树:每层首次展开才列(惰性),已列内容折叠缓存(设计 §12.2/§12.4)。 */
export default function FileTree({ workDir, showBrowsingHint, onOpenFile, refreshSignal }: Props) {
  const [levels, setLevels] = useState<Record<string, LevelState>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const expandedRef = useRef(expanded)

  const fetchLevel = useCallback(async (rel: string, keep: boolean) => {
    setLevels((prev) => ({
      ...prev,
      [rel]: keep && prev[rel]?.status === 'ready' ? prev[rel] : { status: 'loading' },
    }))
    try {
      const listing = await api.listFiles(rel)
      setLevels((prev) => ({
        ...prev,
        [rel]: { status: 'ready', entries: listing.entries, truncated: listing.truncated },
      }))
    } catch (e) {
      setLevels((prev) => ({
        ...prev,
        [rel]: { status: 'failed', message: e instanceof Error ? e.message : String(e) },
      }))
    }
  }, [])

  // 挂载列根;refreshSignal 变化 → 重列根 + 所有已展开层(读取期间保留旧条目)
  useEffect(() => {
    fetchLevel('', true)
    for (const rel of expandedRef.current) fetchLevel(rel, true)
  }, [fetchLevel, refreshSignal])

  const toggleDir = useCallback(
    (rel: string) => {
      const next = new Set(expandedRef.current)
      if (next.has(rel)) {
        next.delete(rel)
      } else {
        next.add(rel)
        if (!levels[rel]) fetchLevel(rel, false)
      }
      expandedRef.current = next
      setExpanded(next)
    },
    [levels, fetchLevel],
  )

  const renderLevel = (rel: string, depth: number) => {
    const level = levels[rel]
    if (!level || level.status === 'loading') {
      return <div className={styles.hint} style={{ paddingLeft: 10 + depth * 18 }}>加载中…</div>
    }
    if (level.status === 'failed') {
      return <div className={`${styles.hint} ${styles.hintError}`} style={{ paddingLeft: 10 + depth * 18 }}>{level.message}</div>
    }
    if (level.entries.length === 0) {
      return <div className={styles.hint} style={{ paddingLeft: 10 + depth * 18 }}>空目录</div>
    }
    return (
      <>
        {level.entries.map((e) => {
          const childRel = rel ? `${rel}/${e.name}` : e.name
          if (e.type === 'directory') {
            const isOpen = expanded.has(childRel)
            return (
              <div key={childRel}>
                <button
                  className={styles.row}
                  style={{ paddingLeft: 10 + depth * 18 }}
                  onClick={() => toggleDir(childRel)}
                  title={childRel}
                >
                  <svg
                    className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ''}`}
                    width={12}
                    height={12}
                    viewBox="0 0 16 16"
                    fill="none"
                    aria-hidden
                  >
                    <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <FolderIcon />
                  <span className={styles.name}>{e.name}</span>
                </button>
                {isOpen && renderLevel(childRel, depth + 1)}
              </div>
            )
          }
          const dimmed = e.type === 'other'
          return (
            <button
              key={childRel}
              className={`${styles.row} ${dimmed ? styles.rowDimmed : ''}`}
              style={{ paddingLeft: 10 + depth * 18 }}
              disabled={dimmed}
              onClick={() => onOpenFile(childRel)}
              title={childRel}
            >
              <span className={styles.chevronSpacer} />
              <FileIcon dimmed={dimmed} />
              <span className={styles.name}>{e.name}</span>
              {typeof e.size === 'number' && e.size >= 1024 && (
                <span className={styles.size}>{Math.round(e.size / 1024)}K</span>
              )}
            </button>
          )
        })}
        {level.truncated && (
          <div className={styles.hint} style={{ paddingLeft: 10 + depth * 18 }}>
            目录过大,仅显示前 {level.entries.length} 项
          </div>
        )}
      </>
    )
  }

  return (
    <div className={styles.root}>
      {showBrowsingHint && (
        <div className={styles.browsingHint}>当前浏览:服务端工作区(切换工作区不改变树根)</div>
      )}
      <div className={styles.rootPath} title={workDir}>{workDir}</div>
      <div className={styles.tree}>{renderLevel('', 0)}</div>
    </div>
  )
}

function FolderIcon() {
  return (
    <svg width={15} height={15} viewBox="0 0 16 16" fill="none" aria-hidden className={styles.icon}>
      <path
        d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 2h4.5A1.5 1.5 0 0 1 14 6.5v5A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5v-7z"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  )
}

function FileIcon({ dimmed }: { dimmed: boolean }) {
  return (
    <svg width={15} height={15} viewBox="0 0 16 16" fill="none" aria-hidden className={styles.icon} data-dimmed={dimmed || undefined}>
      <path
        d="M4 2.5h5L12.5 6v7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-9.5a1 1 0 0 1 1-1z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M9 2.5V6h3.5" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
    </svg>
  )
}
