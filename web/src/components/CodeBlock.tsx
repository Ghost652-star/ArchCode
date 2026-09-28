import { Highlight, themes } from 'prism-react-renderer'
import styles from './CodeBlock.module.css'

/** 扩展名 → Prism 语言(限 prism-react-renderer 内置集,未收录回退 markup 纯文本)。 */
const EXT_LANG: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  ts: 'typescript',
  tsx: 'tsx',
  jsx: 'jsx',
  json: 'json',
  css: 'css',
  scss: 'scss',
  sass: 'sass',
  less: 'less',
  html: 'markup',
  htm: 'markup',
  xml: 'markup',
  svg: 'markup',
  vue: 'markup',
  md: 'markdown',
  yml: 'yaml',
  yaml: 'yaml',
  sh: 'bash',
  bash: 'bash',
  bat: 'bash',
  cmd: 'bash',
  sql: 'sql',
  go: 'go',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  hpp: 'cpp',
  java: 'clike',
  diff: 'diff',
  patch: 'diff',
  makefile: 'makefile',
  graphql: 'graphql',
  gql: 'graphql',
}

export function langFromFilename(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return EXT_LANG[ext] ?? 'markup'
}

interface Props {
  code: string
  language?: string
  lineNumbers?: boolean
}

/** Prism 高亮代码块:主题跟随 data-theme;行号可选(设计 §10.9-1)。 */
export default function CodeBlock({ code, language, lineNumbers = false }: Props) {
  const dark = document.documentElement.dataset.theme === 'dark'
  return (
    <Highlight
      theme={dark ? themes.vsDark : themes.vsLight}
      code={code.replace(/\n$/, '')}
      language={(language ?? 'markup') as never}
    >
      {({ className, style, tokens, getLineProps, getTokenProps }) => (
        <pre
          className={`${className} ${styles.pre} ${lineNumbers ? styles.withNumbers : ''}`}
          style={style}
        >
          {tokens.map((line, i) => {
            const lineProps = getLineProps({ line })
            return (
              <div key={i} {...lineProps} className={`${lineProps.className} ${styles.line}`}>
                {lineNumbers && <span className={styles.lineNo}>{i + 1}</span>}
                <span className={styles.lineText}>
                  {line.map((token, key) => (
                    <span key={key} {...getTokenProps({ token })} />
                  ))}
                </span>
              </div>
            )
          })}
        </pre>
      )}
    </Highlight>
  )
}
