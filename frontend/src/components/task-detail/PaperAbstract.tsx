import { useId, useState } from 'react'

export default function PaperAbstract({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  const id = useId()
  return (
    <div>
      <p
        id={id}
        className={`text-sm text-gray-700 leading-relaxed break-words ${expanded || text.length <= 100 ? '' : 'line-clamp-3'}`}
      >
        {text}
      </p>
      {text.length > 100 && (
        <button
          className="text-xs text-blue-600 mt-1.5 hover:underline"
          aria-expanded={expanded}
          aria-controls={id}
          onClick={(event) => {
            event.stopPropagation()
            setExpanded((value) => !value)
          }}
        >
          {expanded ? '收起摘要' : '展开摘要'}
        </button>
      )}
    </div>
  )
}
