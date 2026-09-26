import styles from './StatusBar.module.css'

/** 底栏:模型名 + 运行状态(工作目录在侧栏,多工作区下不在此展示)。 */
export default function StatusBar({
  model,
  running,
}: {
  model: string
  running: boolean
}) {
  return (
    <div className={styles.root}>
      <span className={styles.dot} data-running={running || undefined} />
      <span className={styles.model}>{model || '—'}</span>
      <span className={styles.spacer} />
      <span className={styles.status}>{running ? '工作中' : '空闲'}</span>
    </div>
  )
}
