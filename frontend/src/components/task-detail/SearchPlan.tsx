import type { TaskInfo } from '../../api/types'

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && !!item.trim()) : []
}

export default function SearchPlan({ task }: { task: TaskInfo }) {
  const deep = task.search_mode === 'deep'
  const rawPlan = task.results?.topic_plan
  const plan = rawPlan && typeof rawPlan === 'object' && !Array.isArray(rawPlan) ? rawPlan as Record<string, unknown> : null
  const queries = deep ? strings(plan?.seed_queries) : strings(task.keywords)
  const concepts = strings(plan?.core_concepts)
  const criteria = strings(plan?.inclusion_criteria)
  const notes = strings(plan?.retrieval_notes)
  const summary = typeof plan?.topic_summary === 'string' ? plan.topic_summary : ''

  if (!queries.length) return <p className="mt-3 text-xs text-slate-500">{task.status === 'running' || task.status === 'pending' ? '查询词确认后将在这里展示。' : '此任务未保存检索查询词。'}</p>

  return <section aria-label="已确认的检索词" className="mt-4 border-t border-slate-100 pt-4">
    {summary && <p className="mb-3 text-sm leading-relaxed text-slate-600">{summary}</p>}
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-medium text-slate-700">{deep ? '检索查询词' : '确认的关键词'} <span className="ml-1 text-xs text-slate-400">{queries.length} 条</span></h3>
      <span className="text-xs text-slate-500">{task.year_from} 年起</span>
    </div>
    {deep ? <ol className="grid gap-2 md:grid-cols-2">
      {queries.map((query, index) => <li key={`${index}-${query}`} className="flex min-w-0 items-start gap-2 rounded-lg bg-blue-50 px-3 py-2.5 text-sm text-blue-800"><span className="shrink-0 pt-0.5 text-[10px] text-blue-500">{String(index + 1).padStart(2, '0')}</span><span className="min-w-0 break-words leading-relaxed">{query}</span></li>)}
    </ol> : <ul className="flex flex-wrap gap-2">{queries.map((query, index) => <li key={`${index}-${query}`} className="max-w-full break-words rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-800">{query}</li>)}</ul>}
    {deep && <p className="mt-3 text-xs leading-relaxed text-slate-400">这些是已确认的初始查询词；后续轮次可能追加查询，完成后可在“结果”中查看逐轮记录。</p>}
    {criteria.length > 0 && <details className="mt-3 text-xs text-slate-600"><summary className="cursor-pointer py-1">纳入标准与范围假设</summary><ul className="mt-2 space-y-2">{[...criteria, ...notes].map((item, i) => <li key={i} className="leading-relaxed">{item}</li>)}</ul></details>}
    {concepts.length > 0 && <details className="mt-3 text-xs text-slate-500"><summary className="cursor-pointer py-1">主题核心概念</summary><ul className="mt-2 space-y-1">{concepts.map((concept, index) => <li key={`${index}-${concept}`} className="leading-relaxed">{concept}</li>)}</ul></details>}
  </section>
}
