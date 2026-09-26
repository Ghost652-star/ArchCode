import styles from './StatusBar.module.css'

/** 底栏:模型名 / 工作目录 / 运行状态(§10.4 状态元素)。 */
export default function StatusBar({
  model,
  workDir,
  running,
}: {
  model: string
  workDir: string
  running: boolean
}) {
  return (
    <div className={styles.root}>
      <span className={styles.dot} data-running={running || undefined} />
      <span className={styles.model}>{model || '—'}</span>
      <span className={styles.sep}>·</span>
      <span className={styles.workDir}>{workDir}</span>
      <span className={styles.spacer} />
      <span className={styles.status}>{running ? '工作中' : '空闲'}</span>
    </div>
  )
}
