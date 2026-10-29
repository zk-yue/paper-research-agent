import { useCallback, useEffect, useState } from 'react'
import {
  cancelReview, deleteReview, fetchInstructionPresets, fetchReviewTemplates, fetchTask,
  resumeReview, reviewExportUrl, translateReview, writeReview,
} from '../../api/client'
import type { CustomReview, InstructionPreset, ReviewTemplate, TaskInfo } from '../../api/types'
import { isReviewActive, reviewEvidenceSummary, reviewHeadings, reviewStatusLabel, selectReview, taskReviews } from '../../lib/reviews'
import MarkdownContent from '../ui/MarkdownContent'
import PresetModal from './PresetModal'
import TemplateModal from './TemplateModal'

type Props = {
  task: TaskInfo
  initialSelection: number[]
  onTaskUpdate: (task: TaskInfo) => void
  onSelectionChange: (indices: Set<number>) => void
}

const statusStyle = (status: string) => status === 'done'
  ? 'bg-emerald-50 text-emerald-700'
  : status === 'failed' || status === 'error'
    ? 'bg-amber-50 text-amber-800'
    : isReviewActive({ status }) ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-600'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求未完成，请重试'
}

function savedContent(review: CustomReview): string {
  return review.content || (review.sections || [])
    .filter(section => section.content)
    .map(section => `## ${section.title}\n\n${section.content}`)
    .join('\n\n')
}

function ReviewProgress({ review }: { review: CustomReview }) {
  const stages = review.stages || []
  const progress = review.progress
  const percent = progress?.total ? Math.min(100, Math.max(0, progress.completed / progress.total * 100)) : 0
  const evidence = review.evidence_stats
  const coverage = review.coverage
  const issues = review.quality?.issues || []
  return <section className="space-y-4" aria-label="综述生成进度">
    {(review.message || progress || stages.length > 0) && <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
        <p className="font-medium text-slate-700" role="status">{review.message || (isReviewActive(review) ? '正在准备综述' : reviewStatusLabel(review.status))}</p>
        {progress && progress.total > 0 && <span className="text-slate-500">{progress.completed} / {progress.total}</span>}
      </div>
      {progress && progress.total > 0 && <div role="progressbar" aria-label={review.message || '当前阶段进度'} aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={Math.min(progress.completed, progress.total)} className="h-1.5 overflow-hidden rounded-full bg-blue-50"><div className="h-full rounded-full bg-blue-400 transition-all" style={{ width: `${percent}%` }} /></div>}
      {stages.length > 0 && <ol className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{stages.map(stage => <li key={stage.key} className={`rounded-lg border p-3 ${stage.status === 'running' ? 'border-blue-200 bg-blue-50/60' : 'border-slate-100 bg-slate-50/50'}`}>
        <p className="text-sm font-medium text-slate-700"><span className="mr-2 text-blue-600">{stage.status === 'done' ? '✓' : stage.status === 'running' ? '◌' : stage.status === 'failed' || stage.status === 'error' ? '!' : '○'}</span>{stage.label}</p>
        {stage.detail && <p className="mt-1 text-xs leading-relaxed text-slate-500">{stage.detail}</p>}
      </li>)}</ol>}
    </div>}
    {(evidence || coverage) && <div className="grid gap-3 sm:grid-cols-2">
      {evidence && <div className="rounded-lg border border-blue-100 bg-blue-50/40 p-3">
        <p className="text-xs font-medium text-slate-500">材料覆盖 · 已选 {evidence.selected} 篇</p>
        <p className="mt-2 text-sm text-slate-800">全文 {evidence.fulltext} · 摘要 {evidence.abstract} · 仅元信息 {evidence.metadata}</p>
        {(typeof evidence.html === 'number' || typeof evidence.pdf === 'number') && <p className="mt-1 text-xs text-slate-500">网页原文 {evidence.html ?? 0} · PDF 原文 {evidence.pdf ?? 0}</p>}
        {evidence.abstract > 0 && <p className="mt-1 text-xs leading-relaxed text-slate-500">摘要来源仅支持摘要中明确陈述的结论。</p>}
      </div>}
      {coverage && <div className="rounded-lg border border-blue-100 bg-blue-50/40 p-3">
        <p className="text-xs font-medium text-slate-500">正文引用覆盖</p>
        <p className="mt-2 text-sm text-slate-800">已引用 {coverage.cited} / {coverage.total} 篇{typeof coverage.assigned === 'number' ? ` · 已分配章节 ${coverage.assigned} 篇` : ''}</p>
        {coverage.uncited_ids && coverage.uncited_ids.length > 0 && <details className="mt-2 text-xs text-slate-600"><summary className="cursor-pointer">尚未引用 {coverage.uncited_ids.length} 篇</summary><p className="mt-2 break-words leading-relaxed">{coverage.uncited_ids.join('、')}</p></details>}
      </div>}
    </div>}
    {review.quality && <div className={`rounded-lg p-3 text-sm ${issues.length || review.quality.invalid_citations > 0 ? 'bg-amber-50 text-amber-900' : 'bg-blue-50 text-blue-800'}`}>
      <p className="font-medium">{review.quality.invalid_citations > 0 ? `${review.quality.invalid_citations} 处引用编号需要检查` : '引用编号检查完成'}</p>
      {issues.length > 0 && <details className="mt-2" open={review.status !== 'done'}><summary className="cursor-pointer text-xs">{issues.length} 项质量检查提示</summary><ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed">{issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></details>}
    </div>}
  </section>
}

export default function ReviewWorkspace({ task, initialSelection, onTaskUpdate, onSelectionChange }: Props) {
  const papers = task.papers || []
  const [selectedPapers, setSelectedPapers] = useState(() => new Set(initialSelection.length ? initialSelection : papers.map((_, i) => i)))
  const [paperQuery, setPaperQuery] = useState('')
  const [instructions, setInstructions] = useState('')
  const [mode, setMode] = useState<'survey' | 'related_work'>('survey')
  const [language, setLanguage] = useState<'zh' | 'en'>('zh')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [connectionError, setConnectionError] = useState('')
  const [templates, setTemplates] = useState<ReviewTemplate[]>([])
  const [presets, setPresets] = useState<InstructionPreset[]>([])
  const [templateId, setTemplateId] = useState('')
  const [showTemplateModal, setShowTemplateModal] = useState(false)
  const [showPresetModal, setShowPresetModal] = useState(false)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [translations, setTranslations] = useState<Record<string, string>>({})
  const [translatingId, setTranslatingId] = useState<string | null>(null)
  const reviews = taskReviews(task)
  const selected = selectReview(reviews, selectedId)
  const active = [...reviews].reverse().find(isReviewActive)
  const hasActive = !!active
  const content = selected ? savedContent(selected) : ''
  const evidenceSummary = selected ? reviewEvidenceSummary(selected) : null
  const headings = reviewHeadings(content)
  const anchorPrefix = `review-${selected?.id || 'draft'}`

  const refresh = useCallback(async () => {
    const fresh = await fetchTask(task.id)
    onTaskUpdate(fresh)
    return fresh
  }, [task.id, onTaskUpdate])

  useEffect(() => {
    let disposed = false
    fetchReviewTemplates().then(value => { if (!disposed) setTemplates(value) }).catch(() => {})
    fetchInstructionPresets().then(value => { if (!disposed) setPresets(value) }).catch(() => {})
    return () => { disposed = true }
  }, [])

  useEffect(() => {
    if (!hasActive && !pendingId) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const fresh = await fetchTask(task.id)
        if (!disposed) {
          onTaskUpdate(fresh)
          setConnectionError('')
          if (pendingId && taskReviews(fresh).some(review => review.id === pendingId)) setPendingId(null)
        }
      } catch (cause) {
        if (!disposed) setConnectionError(`状态同步暂时失败：${errorMessage(cause)}。已保存内容仍可阅读，连接恢复后会继续同步。`)
      }
      if (!disposed) timer = setTimeout(poll, 5000)
    }
    timer = setTimeout(poll, 2000)
    return () => { disposed = true; clearTimeout(timer) }
  }, [task.id, hasActive, pendingId, onTaskUpdate])

  const changeSelection = (next: Set<number>) => {
    setSelectedPapers(next)
    onSelectionChange(next)
  }

  const start = async () => {
    if (!selectedPapers.size || busy || hasActive) return
    setBusy('start')
    setError('')
    try {
      const result = await writeReview(task.id, [...selectedPapers].sort((a, b) => a - b), instructions, templateId, mode, language)
      setSelectedId(result.review_id)
      setPendingId(result.review_id)
      const fresh = await refresh()
      if (taskReviews(fresh).some(review => review.id === result.review_id)) setPendingId(null)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy('')
    }
  }

  const changeRun = async (review: CustomReview, action: 'resume' | 'cancel' | 'delete') => {
    setBusy(action)
    setError('')
    try {
      if (action === 'resume') {
        const result = await resumeReview(task.id, review.id)
        setSelectedId(result.review_id || review.id)
      } else if (action === 'cancel') await cancelReview(task.id, review.id)
      else {
        await deleteReview(task.id, review.id)
        setDeleteId(null)
        if (selectedId === review.id) setSelectedId(null)
      }
      await refresh()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy('')
    }
  }

  const translate = async () => {
    if (!selected || !content) return
    setTranslatingId(selected.id)
    setError('')
    try {
      const translated = await translateReview(content)
      setTranslations(previous => ({ ...previous, [selected.id]: translated }))
    } catch (cause) {
      setError(`翻译未完成：${errorMessage(cause)}`)
    } finally {
      setTranslatingId(null)
    }
  }

  if (!papers.length) return <div className="panel p-8 text-center text-slate-500">暂无检索论文，完成检索后即可生成综述。</div>

  return <div className="space-y-5">
    <section className="panel p-5 sm:p-6" aria-label="创建文献综述">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="text-lg font-semibold text-slate-900">文献综述工作台</h2><p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-500">从检索论文出发，自动整理证据、规划章节、逐章综合比较，再核对引用与覆盖。进度和已完成内容会持续保存。</p></div>
        <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">{papers.length} 篇主列表论文</span>
      </div>
      <details className="mt-4" open={reviews.length === 0}>
      <summary className="cursor-pointer text-sm font-medium text-blue-700">{reviews.length ? '创建新的一版综述' : '生成详细综述'}</summary>
      <details className="mt-5 rounded-lg border border-slate-200 p-3">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">纳入论文 · 已选 {selectedPapers.size} / {papers.length} 篇</summary>
        <div className="mt-3 flex flex-wrap gap-2">
          <input aria-label="筛选待综述论文" value={paperQuery} onChange={event => setPaperQuery(event.target.value)} placeholder="按标题筛选论文" className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" />
          <button className="btn-secondary text-xs" onClick={() => changeSelection(new Set(papers.map((_, i) => i)))}>全选</button>
          <button className="btn-secondary text-xs" onClick={() => changeSelection(new Set())}>清空</button>
        </div>
        <div className="mt-3 max-h-64 space-y-1 overflow-y-auto">
          {papers.map((paper, index) => ({ paper, index })).filter(({ paper }) => paper.title.toLocaleLowerCase().includes(paperQuery.trim().toLocaleLowerCase())).map(({ paper, index }) => <label key={index} className="flex cursor-pointer items-start gap-3 rounded-md p-2 text-sm leading-relaxed hover:bg-blue-50/50"><input type="checkbox" className="mt-1 accent-blue-600" checked={selectedPapers.has(index)} onChange={() => { const next = new Set(selectedPapers); if (next.has(index)) next.delete(index); else next.add(index); changeSelection(next) }} /><span className="min-w-0 text-slate-700">{paper.title}<span className="ml-2 text-xs text-slate-400">{paper.year}</span></span></label>)}
        </div>
      </details>
      <label className="mt-5 block text-sm font-medium text-slate-700" htmlFor="review-instructions">研究侧重点（可选）</label>
      <textarea id="review-instructions" value={instructions} onChange={event => setInstructions(event.target.value)} rows={3} placeholder="例如：深入比较任务规划与记忆的架构、评测协议和局限，重点分析 VLA/WAM 与高层智能体的协作。" className="mt-2 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-relaxed outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" />
      <details className="mt-3 rounded-lg border border-slate-100 px-3 py-2">
        <summary className="cursor-pointer text-xs font-medium text-slate-500">写作选项与模板</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-slate-600">文档类型<select value={mode} onChange={event => setMode(event.target.value as 'survey' | 'related_work')} className="mt-1 w-full rounded-lg border border-slate-200 bg-white p-2 text-sm"><option value="survey">详细综述 · 多章分析与比较</option><option value="related_work">Related Work · 论文中的短节</option></select></label>
          <label className="text-xs text-slate-600">撰写语言<select value={language} onChange={event => setLanguage(event.target.value as 'zh' | 'en')} className="mt-1 w-full rounded-lg border border-slate-200 bg-white p-2 text-sm"><option value="zh">中文</option><option value="en">English</option></select></label>
        </div>
        <div className="mt-3 flex items-end gap-2"><label className="min-w-0 flex-1 text-xs text-slate-600">风格参考<select value={templateId} onChange={event => setTemplateId(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white p-2 text-sm"><option value="">不使用模板</option>{templates.map(template => <option key={template.id} value={template.id}>{template.journal_name}</option>)}</select></label><button className="btn-secondary text-xs" onClick={() => setShowTemplateModal(true)}>管理模板</button></div>
        {presets.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{presets.map(preset => <button key={preset.id} className="rounded-full border border-blue-100 bg-blue-50 px-2 py-1 text-xs text-blue-700" onClick={() => setInstructions(value => value ? `${value}\n${preset.content}` : preset.content)}>+ {preset.label}</button>)}</div>}
        <button className="mt-3 text-xs text-blue-700 hover:underline" onClick={() => setShowPresetModal(true)}>管理写作预设</button>
        {mode === 'related_work' && <p className="mt-3 text-xs leading-relaxed text-slate-500">短节模式用于论文中的相关工作段落；详细、多方向的调研请使用“详细综述”。</p>}
      </details>
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={!!busy || hasActive || !selectedPapers.size || !!pendingId} onClick={start}>{busy === 'start' ? '正在创建…' : mode === 'survey' ? `生成${language === 'zh' ? '中文' : '英文'}详细综述` : '生成 Related Work 短节'}</button>
        {active ? <button className="text-sm text-blue-700 hover:underline" onClick={() => setSelectedId(active.id)}>查看正在生成的综述 →</button> : <span className="text-xs text-slate-500">优先读取网页原文并复用已有资料；缺少全文时会标明摘要依据。</span>}
      </div>
      </details>
    </section>

    {(error || connectionError) && <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-relaxed text-amber-900">{error || connectionError}</div>}
    {pendingId && !reviews.some(review => review.id === pendingId) && <p role="status" className="panel p-4 text-sm text-blue-700">综述任务已创建，正在同步保存的进度…</p>}

    {reviews.length > 0 && <section className="panel p-4" aria-label="综述版本">
      <label htmlFor="review-version" className="mb-2 block text-xs font-medium text-slate-500">已保存的综述 · {reviews.length} 版</label>
      <select id="review-version" value={selected?.id || ''} onChange={event => { setSelectedId(event.target.value); setDeleteId(null); setError('') }} className="w-full rounded-lg border border-slate-200 bg-white p-2 text-sm text-slate-700">{[...reviews].reverse().map(review => <option key={review.id} value={review.id}>{review.inferred_title || (review.mode === 'survey' ? '详细文献综述' : 'Related Work')} · {review.paper_indices?.length || review.paper_titles?.length || 0} 篇 · {reviewStatusLabel(review.status)} · {review.created_at?.replace('T', ' ').slice(0, 16)}</option>)}</select>
    </section>}

    {selected && <section className="panel p-5 sm:p-6" aria-label="综述工作区">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold text-slate-900">{selected.inferred_title || (selected.mode === 'survey' ? '详细文献综述' : 'Related Work')}</h2><span className={`rounded-full px-2 py-0.5 text-xs ${statusStyle(selected.status)}`}>{reviewStatusLabel(selected.status)}</span></div><p className="mt-2 text-xs text-slate-400">{selected.language === 'zh' ? '中文' : selected.language === 'en' ? 'English' : '历史文稿'} · {selected.paper_indices?.length || selected.paper_titles?.length || 0} 篇论文{content ? ` · ${content.length.toLocaleString()} 字符` : ''}{selected.updated_at ? ` · 更新于 ${selected.updated_at.replace('T', ' ').slice(0, 16)}` : ''}</p></div>
        <div className="flex flex-wrap gap-2">
          {content && <a className="btn-secondary text-xs" href={reviewExportUrl(task.id, selected.id)} download>导出 Markdown</a>}
          {isReviewActive(selected) && <button className="btn-secondary text-xs" disabled={!!busy} onClick={() => changeRun(selected, 'cancel')}>{busy === 'cancel' ? '正在停止…' : '停止并保留进度'}</button>}
          {!isReviewActive(selected) && selected.resumable && <button className="btn-primary text-xs" disabled={!!busy || hasActive} onClick={() => changeRun(selected, 'resume')}>{busy === 'resume' ? '正在继续…' : '继续生成'}</button>}
        </div>
      </div>
      {selected.error && <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-relaxed text-amber-900">{selected.error}{selected.resumable ? ' 已完成的材料和章节已保留，可继续生成。' : ''}</p>}
      {selected.status === 'done' && evidenceSummary ? <details key={selected.id} className="rounded-lg border border-slate-100 p-3">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">
          生成记录与来源校验
          <span className="mt-1 block text-xs font-normal leading-relaxed text-slate-500">{evidenceSummary.detail}</span>
          {(evidenceSummary.qualityIssueCount > 0 || evidenceSummary.invalidCitationCount > 0) && <span className="mt-1 block text-xs font-medium text-amber-700">
            {evidenceSummary.qualityIssueCount > 0 && `${evidenceSummary.qualityIssueCount} 项质量检查提示`}
            {evidenceSummary.qualityIssueCount > 0 && evidenceSummary.invalidCitationCount > 0 && ' · '}
            {evidenceSummary.invalidCitationCount > 0 && `${evidenceSummary.invalidCitationCount} 处引用编号需要检查`}
          </span>}
        </summary>
        <div className="mt-4"><ReviewProgress review={selected} /></div>
      </details> : <ReviewProgress review={selected} />}
      {selected.sections && selected.sections.length > 0 && <details className="mt-5 rounded-lg border border-slate-100 p-3" open={isReviewActive(selected)}><summary className="cursor-pointer text-sm font-medium text-slate-700">章节进度 · {selected.sections.filter(section => section.status === 'done').length} / {selected.sections.length}</summary><ol className="mt-3 space-y-2">{selected.sections.map(section => <li key={section.id} className="flex items-start gap-2 text-sm"><span className="mt-0.5 text-blue-600">{section.status === 'done' ? '✓' : section.status === 'running' ? '◌' : '○'}</span><div><p className="text-slate-700">{section.title}</p>{section.error && <p className="mt-1 text-xs text-amber-800">{section.error}</p>}</div></li>)}</ol></details>}
      {content && <div className="mt-6 border-t border-slate-100 pt-4">
        {headings.length > 1 && <details className="mb-5 rounded-lg border border-blue-100 bg-blue-50/30 p-3"><summary className="cursor-pointer text-sm font-medium text-slate-700">章节目录 · {headings.length} 项</summary><nav aria-label="综述目录" className="mt-3 grid gap-2 sm:grid-cols-2">{headings.map(heading => <a key={heading.line} href={`#${anchorPrefix}-line-${heading.line}`} className={`text-xs leading-relaxed text-blue-700 hover:underline ${heading.level === 3 ? 'pl-3' : 'font-medium'}`}>{heading.title}</a>)}</nav></details>}
        {selected.status !== 'done' && <p className="mb-4 text-xs text-slate-500">以下为当前已保存文稿，尚未通过全部质量检查。</p>}
        <article aria-label="综述正文"><MarkdownContent text={content} anchorPrefix={anchorPrefix} /></article>
      </div>}
      {!content && isReviewActive(selected) && <p role="status" className="mt-6 rounded-lg bg-blue-50/50 p-4 text-sm leading-relaxed text-slate-600">正在整理材料与章节计划，首个章节完成后会显示正文。可以离开页面，稍后回来查看。</p>}
      {!isReviewActive(selected) && <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
        <div>{content && selected.language !== 'zh' && <button className="text-sm text-blue-700 hover:underline disabled:opacity-50" disabled={!!translatingId} onClick={translate}>{translatingId === selected.id ? '正在翻译…' : translations[selected.id] ? '重新翻译为中文' : '按需翻译为中文'}</button>}</div>
        {deleteId === selected.id ? <div className="flex items-center gap-2 text-xs"><span className="text-slate-500">删除此综述记录？</span><button className="text-red-700 disabled:opacity-50" disabled={!!busy} onClick={() => changeRun(selected, 'delete')}>确认删除</button><button className="text-slate-500" onClick={() => setDeleteId(null)}>保留</button></div> : <button className="text-xs text-slate-400 hover:text-red-600" onClick={() => setDeleteId(selected.id)}>删除这版综述</button>}
      </div>}
      {translations[selected.id] && <details className="mt-4 rounded-lg border border-blue-100 p-4" open><summary className="cursor-pointer text-sm font-medium text-slate-700">中文译文</summary><div className="mt-4"><MarkdownContent text={translations[selected.id]} anchorPrefix={`${anchorPrefix}-translated`} /></div></details>}
    </section>}
    {showTemplateModal && <TemplateModal templates={templates} onClose={() => { setShowTemplateModal(false); fetchReviewTemplates().then(setTemplates).catch(() => {}) }} />}
    {showPresetModal && <PresetModal presets={presets} onClose={() => { setShowPresetModal(false); fetchInstructionPresets().then(setPresets).catch(() => {}) }} />}
  </div>
}
