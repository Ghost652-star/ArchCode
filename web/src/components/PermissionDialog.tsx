import { useState } from 'react'
import type { PermissionState } from '../types'
import styles from './PermissionDialog.module.css'

/** HITL 卡片:权限询问(Yes/No)与 AskUserQuestion(多选)两种模式(§8.5 Q3)。 */
export default function PermissionDialog({
  permission,
  onAnswer,
}: {
  permission: PermissionState
  onAnswer: (body: { allowed?: boolean; answer?: string }) => void
}) {
  const [selected, setSelected] = useState<string[]>([])
  const isQuestion = permission.question !== null

  const answerQuestion = () => {
    const answer =
      permission.multiSelect && selected.length > 0
        ? selected.join(', ')
        : selected[0] ?? '用户没选择'
    onAnswer({ answer })
  }

  return (
    <div className={styles.overlay}>
      <div className={styles.card}>
        <div className={styles.header}>
          <span className={styles.badge} data-kind={isQuestion ? 'question' : 'permission'} />
          <span className={styles.tool}>{permission.toolName}</span>
        </div>
        <div className={styles.reason}>{permission.reason}</div>
        {isQuestion ? (
          <>
            <div className={styles.question}>{permission.question}</div>
            <div className={styles.options}>
              {(permission.options ?? []).map((option) => {
                const checked = selected.includes(option)
                return (
                  <button
                    key={option}
                    type="button"
                    className={`${styles.option} ${checked ? styles.optionChecked : ''}`}
                    onClick={() =>
                      setSelected((prev) =>
                        permission.multiSelect
                          ? checked
                            ? prev.filter((x) => x !== option)
                            : [...prev, option]
                          : [option],
                      )
                    }
                  >
                    {option}
                  </button>
                )
              })}
            </div>
            <div className={styles.actions}>
              <button className={styles.confirmBtn} onClick={answerQuestion}>
                确认
              </button>
            </div>
          </>
        ) : (
          <div className={styles.actions}>
            <button
              className={styles.denyBtn}
              onClick={() => onAnswer({ allowed: false })}
            >
              拒绝
            </button>
            <button
              className={styles.confirmBtn}
              onClick={() => onAnswer({ allowed: true })}
            >
              允许
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
