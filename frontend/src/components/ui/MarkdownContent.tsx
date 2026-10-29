import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function MarkdownContent({ text, anchorPrefix }: { text: string; anchorPrefix?: string }) {
  return (
    <div className="min-w-0 break-words text-sm leading-7 text-slate-700 [&_p]:my-3 [&_strong]:font-semibold [&_strong]:text-slate-900 [&_li>p]:my-1">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          h1: ({ children, node }) => <h1 id={anchorPrefix ? `${anchorPrefix}-line-${node?.position?.start.line}` : undefined} className="mb-4 mt-6 text-2xl font-bold leading-snug text-slate-900">{children}</h1>,
          h2: ({ children, node }) => <h2 id={anchorPrefix ? `${anchorPrefix}-line-${node?.position?.start.line}` : undefined} className="mb-3 mt-7 border-b border-blue-100 pb-2 text-xl font-semibold leading-snug text-slate-900">{children}</h2>,
          h3: ({ children, node }) => <h3 id={anchorPrefix ? `${anchorPrefix}-line-${node?.position?.start.line}` : undefined} className="mb-2 mt-5 text-lg font-semibold leading-snug text-slate-800">{children}</h3>,
          h4: ({ children }) => <h4 className="mb-2 mt-4 font-semibold text-slate-800">{children}</h4>,
          ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-6">{children}</ul>,
          ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-6">{children}</ol>,
          a: ({ href, title, children }) => <a href={href} title={title} target={href?.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer" className="text-blue-700 underline decoration-blue-200 underline-offset-2 hover:decoration-blue-500">{children}</a>,
          blockquote: ({ children }) => <blockquote className="my-4 border-l-4 border-blue-200 bg-blue-50/50 px-4 py-1 text-slate-600">{children}</blockquote>,
          pre: ({ children }) => <pre className="my-4 overflow-x-auto rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs leading-6 [&_code]:bg-transparent [&_code]:p-0">{children}</pre>,
          code: ({ children, className }) => <code className={`rounded bg-blue-50 px-1 py-0.5 font-mono text-[0.9em] ${className || ''}`}>{children}</code>,
          table: ({ children }) => <div className="my-4 overflow-x-auto rounded-lg border border-blue-100"><table className="w-full min-w-[36rem] border-collapse text-sm">{children}</table></div>,
          thead: ({ children }) => <thead className="bg-blue-50 text-slate-800">{children}</thead>,
          th: ({ children, style }) => <th style={style} className="border-b border-blue-100 px-3 py-2 text-left font-semibold">{children}</th>,
          td: ({ children, style }) => <td style={style} className="border-b border-slate-100 px-3 py-2 align-top">{children}</td>,
          hr: () => <hr className="my-6 border-blue-100" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
