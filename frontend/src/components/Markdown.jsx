import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * 渲染 AI 生成的报告正文。
 *
 * 免责提示做在这里，而不是各个调用点：这个项目里 `Markdown` **只**用来渲染 AI 产出的
 * 文本（整场复盘、玩家报告、刚生成还没落盘的草稿），放这里就不可能漏掉哪一处 ——
 * 包括分享出去、别人免登录打开的那个只读页面。
 * 以后如果要渲染非 AI 的内容，别再复用它。
 */
export default function Markdown({ children }) {
  if (!children) return null
  return (
    <>
      <p className="ai-notice">分析内容由 AI 生成，结论可能脱离实际情况，仅供参考。</p>
      <div className="markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
      </div>
    </>
  )
}
