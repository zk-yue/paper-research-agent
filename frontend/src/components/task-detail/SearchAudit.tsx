import type { TaskInfo } from '../../api/types'

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object') : []
}
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}
function safeUrl(value: unknown): string | undefined {
  return typeof value === 'string' && /^https?:\/\//i.test(value) ? value : undefined
}

export default function SearchAudit({ task }: { task: TaskInfo }) {
  const rounds = records(task.results?.iteration_log)
  const screening = object(task.results?.screening)
  const hasScreening = Object.keys(screening).length > 0
  const pending = records(screening.pending)
  const excluded = records(screening.excluded)
  const supporting = records(screening.supporting)
  const included = records(screening.included)
  const coverageLabels: Record<string, string> = { bounded_results: '部分查询达到返回数量上限，仍可能有未取回的相关论文。', partial_sources: '部分检索来源失败或未完成，当前结果存在覆盖缺口，请展开逐轮记录查看原因。', budget_exhausted: '已达到本次检索轮数上限，仍可能有遗漏。', supported_coverage: '计划中的各方向已找到支持文献；这不代表已穷尽所有论文。', no_new_queries: '当前没有新的查询词，检索结束；仍可扩大范围继续查找。' }
  const rawStatus = task.results?.coverage_status
  const status = typeof rawStatus === 'string' ? (coverageLabels[rawStatus] || rawStatus) : ''
  if (!rounds.length && !Object.keys(screening).length) return null
  return <section className="panel mb-6 p-5" aria-label="检索与筛选记录">
    <h2 className="mb-2 font-semibold text-slate-800">检索与筛选记录</h2>
    <p className="mb-4 text-sm text-slate-500">{task.year_from} 年起 · 当前论文 {task.papers?.length || 0} 篇{hasScreening && <> · 主列表 {included.length} 篇 · 补充阅读 {supporting.length} 篇 · 待复核 {pending.length} 篇 · 排除 {excluded.length} 篇</>}</p>
    {!hasScreening && <p className="mb-3 text-xs text-slate-500">此任务尚无逐篇筛选记录；旧任务的排除与待复核数量无法回溯。</p>}
    {status && <p className="mb-4 rounded-lg bg-blue-50 p-3 text-sm text-blue-800">{status}</p>}
    <p className="mb-4 text-xs text-slate-500">主列表论文用于总结；补充阅读提供可迁移或部分关联的思路。待复核论文未通过完整证据校验，不计入相关论文；检索结束不代表已穷尽所有文献。</p>
    <div className="space-y-3">{rounds.map((entry, index) => {
      const diagnostics = records(entry.search_diagnostics ?? entry.diagnostics)
      const warnings = texts(entry.warnings)
      return <details key={index} className="rounded-lg border border-slate-200 p-3">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">{entry.kind === 'references' ? '参考文献追溯' : `第 ${String(entry.round)} 轮`}：命中 {String(entry.found ?? 0)} 篇，新增纳入 {String(entry.new_retained ?? 0)} 篇</summary>
        <ul className="my-3 space-y-1 text-xs text-slate-600">{texts(entry.queries).map((query, i) => <li key={i} className="break-words">{query}</li>)}</ul>
        {warnings.map((warning, i) => <p key={i} className="my-2 text-xs text-amber-800">{warning}</p>)}
        {diagnostics.length > 0 && <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b text-slate-500"><th className="p-2">来源 / 查询</th><th className="p-2">状态</th><th className="p-2">返回</th></tr></thead><tbody>{diagnostics.map((row, i) => <tr key={i} className="border-b border-slate-100 align-top"><td className="max-w-sm break-words p-2"><span className="font-medium">{String(row.source || '')}</span> <span className="text-slate-400">{({ exact: '精确', relaxed: '放宽短语', recent: '当年补检', references: '引用追溯' }[String(row.variant)] || '')}{row.year_from ? ` · ${String(row.year_from)} 年起` : ''}</span><br />{String(row.query || '')}{row.effective_query ? <details className="mt-1 text-slate-400"><summary className="cursor-pointer">实际查询</summary>{String(row.effective_query)}</details> : null}</td><td className="p-2">{({ success: '成功', empty: '无命中', error: '失败', skipped: '未执行', no_reference_metadata: '缺少引用信息' }[String(row.status)] || String(row.status || ''))}{(row.truncated || row.capped) ? ' · 达到数量上限' : ''}{row.error ? <p className="mt-1 max-w-xs break-words text-amber-800">{String(row.error)}</p> : null}</td><td className="p-2">{String(row.count ?? 0)}</td></tr>)}</tbody></table></div>}
      </details>
    })}</div>
    {([['补充阅读', supporting], ['待复核', pending], ['已排除', excluded]] as const).map(([label, papers]) => papers.length > 0 && <details key={label} className="mt-4 rounded-lg border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-medium text-slate-700">{label} · {papers.length} 篇</summary><ul className="mt-3 space-y-4">{papers.map((paper, index) => <li key={index} className="text-sm"><a href={safeUrl(paper.url)} target="_blank" rel="noreferrer" className="text-blue-700 hover:underline">{String(paper.title || '未命名论文')}</a><p className="mt-1 text-xs leading-relaxed text-slate-500">{String(paper.relevance_reason || paper.screening_reason || '证据不足，需要人工复核')}</p></li>)}</ul></details>)}
  </section>
}
