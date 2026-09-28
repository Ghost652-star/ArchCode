import { Component, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/** 渲染崩溃兜底:任何子树抛错只挂这张错误卡,不再整页黑屏(AskUserQuestion 事故教训)。 */
export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: { componentStack?: string }) {
    console.error('[ArchCode] 渲染崩溃:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            height: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'var(--ac-bg-base)',
            color: 'var(--ac-label-primary)',
            fontFamily: 'var(--ac-font-family)',
          }}
        >
          <div
            style={{
              maxWidth: 560,
              padding: '24px 28px',
              borderRadius: 14,
              border: '0.5px solid var(--ac-border-l2)',
              background: 'var(--ac-bg-sidebar, var(--ac-bg-base))',
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>
              页面渲染出错了
            </div>
            <pre
              style={{
                margin: '0 0 16px',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                fontSize: 13,
                lineHeight: 1.6,
                color: 'var(--ac-state-error)',
                maxHeight: 200,
                overflow: 'auto',
              }}
            >
              {this.state.error.message}
            </pre>
            <button
              onClick={() => this.setState({ error: null })}
              style={{
                padding: '8px 16px',
                marginRight: 8,
                borderRadius: 10,
                border: '0.5px solid var(--ac-border-l2)',
                color: 'var(--ac-label-primary)',
                fontSize: 14,
              }}
            >
              重试
            </button>
            <button
              onClick={() => location.reload()}
              style={{
                padding: '8px 16px',
                borderRadius: 10,
                border: '0.5px solid var(--ac-border-l2)',
                color: 'var(--ac-label-primary)',
                fontSize: 14,
              }}
            >
              刷新页面
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
