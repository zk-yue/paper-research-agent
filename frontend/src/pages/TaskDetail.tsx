import SearchAudit from '../components/task-detail/SearchAudit'
import SearchPlan from '../components/task-detail/SearchPlan'
import PaperAbstract from '../components/task-detail/PaperAbstract'
import StatusBadge from '../components/ui/StatusBadge'
import { filterPapers, toggleVisibleSelection, downloadCounts } from '../lib/research'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, useSearchParams, Link } from 'react-router-dom'
import SimpleMarkdown from '../components/ui/MarkdownContent'
import ReviewWorkspace from '../components/task-detail/ReviewWorkspace'
import { fetchTask, cancelTask, retryTask, subscribeWs, downloadPapers, listDownloadedPapers, fetchPromptLog, deletePromptLogEntry } from '../api/client'
import type { TaskInfo, WsMessage, StepInfo, PromptLogEntry } from '../api/types'
import { taskTabFromSearch, searchWithTaskTab, type TaskTab } from '../lib/task-navigation'

const STEP_ICONS: Record<string, string> = {
  pending: '○',
  running: '⟳',
  done: '✓',
  error: '✗',
  skipped: '—',
}

const STEP_COLORS: Record<string, string> = {
  pending: 'text-gray-400',
  running: 'text-blue-500',
  done: 'text-emerald-500',
  error: 'text-red-500',
  skipped: 'text-gray-300',
}

function formatTimestamp(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}


export default function TaskDetail() {
  const { id } = useParams<{ id: string }>()!
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = taskTabFromSearch(searchParams)
  const setTab = (nextTab: TaskTab) => {
    if (searchParams.get('tab') === nextTab) return
    setSearchParams(current => searchWithTaskTab(current, nextTab))
  }
  const [task, setTask] = useState<TaskInfo | null>(null)
  const [paperQuery, setPaperQuery] = useState('')
  const [paperSort, setPaperSort] = useState('original')
  const [actionError, setActionError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [canceling, setCanceling] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [selectedPapers, setSelectedPapers] = useState<Set<number>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadProgress, setDownloadProgress] = useState<{ current: number; total: number; status: string } | null>(null)
  const [downloadedTitles, setDownloadedTitles] = useState<Set<string>>(new Set())
  const [reviewSelected, setReviewSelected] = useState<Set<number>>(new Set())
  // Prompt log state
  const [promptLog, setPromptLog] = useState<PromptLogEntry[]>([])
  const [expandedPromptId, setExpandedPromptId] = useState<string | null>(null)
  const wsRef = useRef<{ close: () => void } | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const [streamContentByStep, setStreamContentByStep] = useState<Record<number, string>>({})

  const loadTask = useCallback(async () => {
    if (!id) return
    try {
      const data = await fetchTask(id)
      setTask(data)
      setLoadError('')
    } catch (e) {
      setLoadError(getErrorMessage(e, '加载失败，请重试'))
    } finally {
      setLoading(false)
    }
  }, [id])

  const loadDownloaded = useCallback(async () => {
    if (!id) return
    try {
      const data = await listDownloadedPapers(id)
      setDownloadedTitles(new Set(data.paper_ids))
    } catch {
      // silently ignore
    }
  }, [id])

  useEffect(() => {
    if (!id) return
    let active = true
    listDownloadedPapers(id)
      .then((data) => {
        if (active) setDownloadedTitles(new Set(data.paper_ids))
      })
      .catch(() => {
        // silently ignore
      })
    return () => {
      active = false
    }
  }, [id])

  // Load prompt log when tab changes
  useEffect(() => {
    if (tab === 'prompt' && id) {
      fetchPromptLog(id).then((logs) => setPromptLog(logs.reverse())).catch(() => {})
    }
  }, [tab, id])

  useEffect(() => {
    if (!id) return

    const ws = subscribeWs(id, (msg: WsMessage) => {
      if (msg.type === 'resync_required') {
        loadTask()
        fetchPromptLog(id).then(logs => setPromptLog(logs.reverse())).catch(console.error)
      }
      if (msg.type === 'step_update') {
        setTask((prev) => {
          if (!prev) return prev
          const steps = prev.steps.map((s) => ({ ...s }))
          if (msg.step_idx < steps.length) {
            steps[msg.step_idx].status = msg.status as StepInfo['status']
            steps[msg.step_idx].detail = msg.detail
          }
          return { ...prev, steps }
        })
        if (msg.detail) {
          setLogLines((prev) => [...prev, msg.detail])
        }
      }
      if (msg.type === 'stream_content') {
        // Append streaming content for this step
        const stepIdx = msg.step_idx
        setStreamContentByStep((prev) => ({
          ...prev,
          [stepIdx]: (prev[stepIdx] || '') + msg.content,
        }))
        // Update task with the accumulated streaming content
        setTask((prev) => {
          if (!prev) return prev
          const results = { ...(prev.results || {}) }
          // Map step index to result key
          const keyMap: Record<number, string> = {
            3: 'literature_review',
          }
          const key = keyMap[stepIdx]
          if (key) {
            const current = typeof results[key] === 'string' ? results[key] : ''
            results[key] = current + msg.content
          }
          return { ...prev, results }
        })
      }
      if ((msg.type === 'init' || msg.type === 'task_update') && msg.task) {
        if (msg.type === 'init') {
          setStreamContentByStep({})
        }
        setTask(msg.task)
      }
      if (msg.type === 'cancelled') {
        loadTask()
      }
      if (msg.type === 'download_progress') {
        const idx = msg.paper_idx
        const total = msg.total
        if (idx >= total) {
          setDownloadProgress({ current: total, total, status: msg.status })
        } else {
          setDownloadProgress({ current: idx, total, status: msg.status })
        }
      }
      if (msg.type === 'prompt_log_entry' && msg.entry) {
        setPromptLog((prev) => {
          const idx = prev.findIndex(
            (e) => e.id === msg.entry.id,
          )
          if (idx >= 0) {
            const next = [...prev]
            next[idx] = msg.entry
            return next
          }
          return [msg.entry, ...prev]
        })
      }
    })

    wsRef.current = ws

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [id, loadTask])

  const taskStatus = task?.status

  useEffect(() => {
    if (taskStatus !== 'running' && taskStatus !== 'pending') return

    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      if (!document.hidden) await loadTask()
      if (!disposed) timer = setTimeout(poll, 10000)
    }
    timer = setTimeout(poll, 10000)
    return () => { disposed = true; clearTimeout(timer) }
  }, [taskStatus, loadTask])

  useEffect(() => {
    if (!id) return
    let active = true
    fetchTask(id)
      .then((data) => {
        if (active) { setTask(data); setLoadError('') }
      })
      .catch((e) => {
        if (active) setLoadError(getErrorMessage(e, '加载失败，请重试'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [id])

  const handleCancel = async () => {
    if (!id) return
    setActionError('')
    setCanceling(true)
    try {
      await cancelTask(id)
      loadTask()
    } catch (e) {
      setActionError(getErrorMessage(e, '停止失败，请重试'))
    } finally {
      setCanceling(false)
    }
  }

  const handleRetry = async () => {
    if (!id) return
    setActionError('')
    setRetrying(true)
    try {
      const result = await retryTask(id)
      navigate(`/task/${result.task_id}`)
    } catch (e) {
      setActionError(getErrorMessage(e, '重试失败，请稍后重试'))
    } finally {
      setRetrying(false)
    }
  }

  const tabs: { key: TaskTab; label: string }[] = [
    { key: 'progress', label: '执行进度' },
    { key: 'papers', label: '论文列表' },
    { key: 'results', label: '结果' },
    { key: 'review', label: '综述撰写' },
    { key: 'prompt', label: 'Prompt' },
    { key: 'log', label: '日志' },
  ]

  const visiblePapers = filterPapers(task?.papers || [], paperQuery, paperSort)
  const allVisibleSelected = visiblePapers.length > 0 && visiblePapers.every(({ index }) => selectedPapers.has(index))
  const handleSelectAll = () => {
    setSelectedPapers(previous => toggleVisibleSelection(previous, visiblePapers.map(({ index }) => index)))
  }

  const handleSelectPaper = (idx: number) => {
    setSelectedPapers((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx); else next.add(idx)
      return next
    })
  }

  const handleDownloadSelected = async () => {
    if (!id || selectedPapers.size === 0) return
    setDownloading(true)
    setDownloadProgress({ current: 0, total: selectedPapers.size, status: '开始下载...' })
    try {
      const response = await downloadPapers(id, Array.from(selectedPapers).sort((a, b) => a - b))
      const counts = downloadCounts(response.results)
      setDownloadProgress({ current: counts.total, total: selectedPapers.size, status: `下载完成：${counts.success} 篇成功，${counts.failed} 篇失败。${counts.failed ? '请检查论文 PDF 链接后重试。' : ''}` })
      loadDownloaded()
    } catch (e) {
      console.error('Failed to download papers:', e)
      setDownloadProgress({ current: 0, total: selectedPapers.size, status: getErrorMessage(e, '下载失败') })
    } finally {
      setDownloading(false)
    }
  }

  if (loading) {
    return <div className="text-center py-16 text-gray-400">加载中...</div>
  }

  if (!task) {
    return (
      <div className="text-center py-16">
        <p role="alert" className="text-gray-500 mb-4">{loadError || '任务不存在'}</p>
        <button className="btn-secondary mr-3" onClick={loadTask}>重新加载</button>
        <Link to="/" className="text-blue-600 hover:text-blue-500">返回任务面板</Link>
      </div>
    )
  }

  const isRunning = task.status === 'running' || task.status === 'pending'
  const totalSteps = task.steps.length
  const doneSteps = task.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length
  const progress = totalSteps > 0 ? Math.round((doneSteps / totalSteps) * 100) : 0

  const results = task.results || {}
  const isDeepMode = task.search_mode === 'deep'
  const resultKeys = Object.keys(results).filter((k) => {
    if (!results[k]) return false
    if (k === 'literature_review' && results.deep_research_summary) return false
    if (['iteration_log', 'topic_plan', 'custom_reviews', 'screening', 'coverage_status'].includes(k)) return false
    return true
  })

  return (
    <div>
      <Link to="/" className="inline-block mb-5 text-sm text-slate-500 hover:text-blue-600">← 研究任务</Link>
      {actionError && <div role="alert" className="error-banner mb-4">{actionError}</div>}
      {loadError && <div role="alert" className="error-banner mb-4">更新失败：{loadError}<button className="ml-3 underline" onClick={loadTask}>重试</button></div>}
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-start justify-between gap-4 mb-2">
          <div>
            <h1 className="text-2xl font-semibold text-gray-900 break-words">
              {isDeepMode && task.topic ? task.topic : (task.keywords || []).join(', ')}
            </h1>
            <div className="flex items-center gap-2 mt-1">
              <p className="text-sm text-gray-400">ID: {task.id}</p>
              {isDeepMode && (
                <span className="text-xs px-2 py-0.5 bg-indigo-50 text-indigo-600 rounded font-medium">
                  深度检索 · {task.deep_rounds || 3} 轮 · {task.quick_mode ? '快速检索' : '完整检索'}
                </span>
              )}
            </div>
          </div>
          <StatusBadge status={task.status} />
        </div>

        {/* Action buttons */}
        <div className="flex gap-2 mt-3">
          {isRunning && (
            <button
              onClick={handleCancel}
              disabled={canceling}
              className="px-4 py-2 bg-red-50 hover:bg-red-100 text-red-600 border border-red-200 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            >
              {canceling ? '停止中…' : '停止运行'}
            </button>
          )}
          {(task.status === 'failed' || task.status === 'cancelled') && (
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            >
              {retrying ? '创建新任务中…' : '重新运行'}
            </button>
          )}
          <button
            onClick={() => navigate('/')}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm font-medium transition-colors"
          >
            返回
          </button>
        </div>
      </div>

      {/* Progress overview (always visible when running) */}
      {isRunning && (
        <div className="mb-6 p-4 bg-white border border-gray-200 rounded-lg">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700">总体进度</span>
            <span className="text-sm text-gray-500">{progress}%</span>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200 mb-6 overflow-x-auto">
        <div className="flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              aria-pressed={tab === t.key}
              className={`shrink-0 whitespace-nowrap px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                tab === t.key
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-400 hover:text-gray-700'
              }`}
            >
              {t.label}{t.key === 'papers' && <span className="ml-2 text-xs opacity-60">{task.papers?.length || 0}</span>}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      {tab === 'progress' && (
        <div className="space-y-3">
          {task.steps.map((step, i) => {
            const streamContent = streamContentByStep[i] || ''
            return (
              <div
                key={i}
                className={`p-4 rounded-lg border transition-all ${
                  step.status === 'running'
                    ? 'bg-blue-50 border-blue-200'
                    : step.status === 'error'
                      ? 'bg-red-50 border-red-200'
                      : 'bg-white border-gray-200'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className={`text-lg ${STEP_COLORS[step.status] || 'text-gray-400'}`}>
                    {STEP_ICONS[step.status] || '○'}
                  </span>
                  <div className="flex-1">
                    <span className={`text-sm font-medium ${
                      step.status === 'running' ? 'text-blue-600' :
                      step.status === 'done' ? 'text-emerald-600' :
                      step.status === 'error' ? 'text-red-600' :
                      step.status === 'skipped' ? 'text-gray-400' :
                      'text-gray-500'
                    }`}>
                      {step.label}
                    </span>
                    {step.detail && (
                      <p className="text-xs text-gray-400 mt-0.5">{step.detail}</p>
                    )}
                  </div>
                  {step.status === 'error' && (
                    <span className="text-xs text-red-600">出错</span>
                  )}
                </div>
                {i === 0 && <SearchPlan task={task} />}
                {/* Streaming content preview */}
                {step.status === 'running' && streamContent && (
                  <div className="mt-3 p-3 bg-white/70 rounded-lg max-h-48 overflow-y-auto border border-blue-100">
                    <p className="text-xs text-blue-500 mb-1">▸ 正在生成：</p>
                    <pre className="text-xs text-gray-600 whitespace-pre-wrap font-mono">
                      {streamContent.slice(-500)}
                    </pre>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {tab === 'papers' && (
        <div>
          <div className="panel flex flex-wrap items-center gap-3 p-3 mb-4">
            <input aria-label="搜索论文" placeholder="搜索标题、作者或中文摘要" className="form-input min-w-48 flex-1" value={paperQuery} onChange={e => setPaperQuery(e.target.value)} />
            <select aria-label="论文排序" className="form-input !w-auto" value={paperSort} onChange={e => setPaperSort(e.target.value)}><option value="original">原始顺序</option><option value="relevance">相关度优先</option><option value="year">年份最新</option></select>
            <span className="text-xs text-slate-500">{visiblePapers.length} / {task.papers?.length || 0} 篇</span>
          </div>
          {/* Download controls */}
          {task.papers && task.papers.length > 0 && (
            <div className="mb-4 flex flex-wrap gap-3 items-center justify-between">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  disabled={!visiblePapers.length}
                  onClick={handleSelectAll}
                  className="text-sm text-blue-600 hover:text-blue-500 font-medium"
                >
                  {allVisibleSelected ? '取消当前选择' : '全选当前结果'}
                </button>
                <span className="text-sm text-gray-400">
                  下载已选 {selectedPapers.size}/{task.papers?.length || 0}
                </span>
                <span className="text-gray-300">|</span>
                <span className="text-sm text-purple-500 font-medium">
                  综述已选 {reviewSelected.size}/{task.papers?.length || 0}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
              <button className="btn-secondary" disabled={!selectedPapers.size} onClick={() => { setReviewSelected(new Set(selectedPapers)); setTab('review') }}>用所选论文撰写综述</button>
              <button
                onClick={handleDownloadSelected}
                disabled={downloading || selectedPapers.size === 0}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-100 disabled:text-gray-400 text-white rounded-lg text-sm font-medium transition-colors"
              >
                {downloading ? '下载中...' : '下载选中论文'}
              </button>
              </div>
            </div>
          )}

          {/* Download progress */}
          {downloadProgress && (
            <div className="mb-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-blue-700">{downloadProgress.status}</span>
                <span className="text-xs text-blue-500">{downloadProgress.current}/{downloadProgress.total}</span>
              </div>
              <div className="h-1.5 bg-blue-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-500"
                  style={{ width: `${(downloadProgress.current / downloadProgress.total) * 100}%` }}
                />
              </div>
            </div>
          )}

          {/* Paper list */}
          {visiblePapers.length > 0 ? (
            <div className="space-y-3">
              {visiblePapers.map(({ paper, index: i }) => (
                <div
                  key={i}
                  className={`p-4 border rounded-lg transition-all cursor-pointer ${
                    selectedPapers.has(i)
                      ? 'bg-blue-50 border-blue-300'
                      : 'bg-white border-gray-200'
                  }`}
                  onClick={() => handleSelectPaper(i)}
                >
                  <div className="flex items-start gap-3">
                    <div className="pt-1 shrink-0">
                      <input
                        type="checkbox"
                        aria-label={`选择论文 ${paper.title}`}
                        checked={selectedPapers.has(i)}
                        onChange={() => handleSelectPaper(i)}
                        onClick={(e) => e.stopPropagation()}
                        className="w-4 h-4 rounded border-gray-300 bg-white text-blue-600 focus:ring-blue-500 cursor-pointer"
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-medium text-gray-900 mb-1 flex items-center gap-2">
                        <span>{paper.title}</span>
                        {reviewSelected.has(i) && (
                          <span className="text-xs px-2 py-0.5 bg-purple-50 text-purple-600 rounded font-medium whitespace-nowrap">
                            待综述
                          </span>
                        )}
                      </h3>
                      <p className="text-xs text-gray-400 mb-2 truncate">{paper.authors}</p>
                      {paper.summary_cn && (
                        <PaperAbstract text={paper.summary_cn} />
                      )}
                      {paper.relevance_reason && (
                        <p className="text-xs text-gray-500 mt-1.5 italic">相关理由：{paper.relevance_reason}</p>
                      )}
                      {paper.relevance_evidence && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">筛选依据（摘要原文）</summary><blockquote className="mt-2 border-l-2 border-blue-200 pl-3 leading-relaxed">{paper.relevance_evidence}</blockquote></details>}
                      {!!paper.citation_from?.length && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">引用来源 · {paper.citation_from.length} 篇</summary><ul className="mt-2 space-y-1">{paper.citation_from.map((parent, i) => <li key={i}>{parent.title || parent.paper_id || parent.openalex_id}</li>)}</ul></details>}
                      {!!paper.matched_queries?.length && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">命中的查询词 · {paper.matched_queries.length}</summary><ul className="mt-2 space-y-1">{paper.matched_queries.map((query, i) => <li key={i}>{query}</li>)}</ul></details>}
                      <div className="flex flex-wrap gap-2 mt-2">
                        {paper.paper_id && downloadedTitles.has(paper.paper_id) && (
                          <span className="text-xs px-2 py-0.5 bg-emerald-50 text-emerald-600 rounded font-medium">
                            ✓ 已下载
                          </span>
                        )}
                        {paper.relevance_score && (
                          <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-600 rounded">
                            相关度: {paper.relevance_score}/5
                          </span>
                        )}
                        {paper.year && (
                          <span className="text-xs px-2 py-0.5 bg-gray-100 text-gray-600 rounded">
                            {paper.year}
                          </span>
                        )}
                        {paper.source && (
                          <span className="text-xs px-2 py-0.5 bg-gray-100 text-gray-600 rounded">
                            {paper.source}
                          </span>
                        )}
                        {typeof paper.citations === 'number' && paper.citations > 0 && (
                          <span className="text-xs px-2 py-0.5 bg-amber-50 text-amber-700 rounded">
                            引用 {paper.citations}
                          </span>
                        )}
                        {paper.discovery_round && (
                          <span className="text-xs px-2 py-0.5 bg-indigo-50 text-indigo-600 rounded">
                            第 {paper.discovery_round} 轮发现
                          </span>
                        )}
                        {paper.paper_id && (
                          <span className="text-xs px-2 py-0.5 bg-gray-100 text-gray-500 rounded">
                            arxiv: {paper.paper_id}
                          </span>
                        )}
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            setReviewSelected((prev) => {
                              const next = new Set(prev)
                              if (next.has(i)) next.delete(i); else next.add(i)
                              return next
                            })
                          }}
                          className={`text-xs px-2 py-0.5 rounded font-medium transition-colors ${
                            reviewSelected.has(i)
                              ? 'bg-purple-100 text-purple-600 hover:bg-purple-200'
                              : 'bg-gray-50 text-gray-400 hover:bg-gray-100'
                          }`}
                        >
                          {reviewSelected.has(i) ? '加入综述 ✓' : '加入综述'}
                        </button>
                      </div>
                    </div>
                    {paper.url && (
                      <a
                        href={paper.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-blue-600 hover:text-blue-500 whitespace-nowrap"
                      >
                        原文 →
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="panel empty-state"><h2>{task.papers?.length ? '没有匹配的论文' : '暂无论文数据'}</h2>{paperQuery && <button className="btn-secondary mt-3" onClick={() => setPaperQuery('')}>清除搜索</button>}</div>
          )}
        </div>
      )}

      {tab === 'results' && (
        <div>
          <SearchAudit task={task} />
          {resultKeys.length > 0 ? (
            <div className="space-y-6">
              {resultKeys.map((key) => {
                const isStreaming = isRunning && typeof results[key] === 'string' && resultKeys.length < Object.keys(task.results || {}).length
                const titleMap: Record<string, string> = {
                  literature_review: '📚 文献调研',
                  deep_research_summary: '🧠 深度检索总结',
                }
                return (
                  <div key={key} className="p-5 bg-white border border-gray-200 rounded-lg">
                    <h3 className="text-base font-semibold text-gray-900 mb-4">
                      {titleMap[key] || key}
                      {isStreaming && <span className="ml-2 inline-block text-xs px-2 py-0.5 bg-blue-100 text-blue-600 rounded-full animate-pulse">生成中...</span>}
                    </h3>
                  <SimpleMarkdown text={String(results[key])} />
                    </div>
                  )
                })}
            </div>
          ) : (
            <div className="text-center py-12 text-gray-400">
              {isRunning ? '等待结果生成...' : '暂无结果'}
            </div>
          )}
        </div>
      )}

      {tab === 'review' && task && (
        <ReviewWorkspace
          key={task.id}
          task={task}
          initialSelection={Array.from(reviewSelected)}
          onTaskUpdate={setTask}
          onSelectionChange={setReviewSelected}
        />
      )}

      {tab === 'prompt' && (
        <div className="space-y-3">
          {promptLog.length > 0 ? (
            promptLog.map((entry) => (
              <div
                key={entry.id}
                className="p-4 bg-white border border-gray-200 rounded-lg"
              >
                <div
                  className="flex items-center justify-between cursor-pointer"
                  onClick={() => setExpandedPromptId(expandedPromptId === entry.id ? null : entry.id)}
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-500 font-mono">{formatTimestamp(entry.timestamp)}</span>
                    <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded text-xs font-medium">
                      {entry.agent}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      entry.status === 'success' ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-600'
                    }`}>
                      {entry.status === 'success' ? '成功' : '失败'}
                    </span>
                    <span className="text-xs text-gray-400">{entry.duration_ms}ms</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        if (confirm('确定删除此记录？')) {
                          deletePromptLogEntry(id!, entry.id)
                            .then(() => {
                              setPromptLog(prev => prev.filter(e => e.id !== entry.id))
                            })
                            .catch(err => console.error('Delete failed:', err))
                        }
                      }}
                      className="text-xs text-gray-400 hover:text-red-500 transition-colors px-2 py-0.5 rounded hover:bg-red-50"
                    >
                      删除
                    </button>
                    <span className="text-xs text-gray-400">
                      {expandedPromptId === entry.id ? '收起' : '展开'}
                    </span>
                  </div>
                </div>
                {entry.response_preview && (
                  <p className="text-xs text-gray-500 mt-2 line-clamp-2">{entry.response_preview}</p>
                )}
                {expandedPromptId === entry.id && (
                  <div className="mt-3 space-y-3 border-t border-gray-100 pt-3">
                    {entry.system_prompt && (
                      <div>
                        <p className="text-xs font-medium text-gray-600 mb-1">System Prompt</p>
                        <pre className="text-xs bg-gray-50 border border-gray-200 rounded p-3 whitespace-pre-wrap font-mono max-h-60 overflow-y-auto">
                          {entry.system_prompt}
                        </pre>
                      </div>
                    )}
                    <div>
                      <p className="text-xs font-medium text-gray-600 mb-1">User Prompt</p>
                      <pre className="text-xs bg-gray-50 border border-gray-200 rounded p-3 whitespace-pre-wrap font-mono max-h-60 overflow-y-auto">
                        {entry.user_prompt}
                      </pre>
                    </div>
                    {entry.response_preview && (
                      <div>
                        <p className="text-xs font-medium text-gray-600 mb-1">Response</p>
                        <pre className="text-xs bg-gray-50 border border-gray-200 rounded p-3 whitespace-pre-wrap font-mono max-h-60 overflow-y-auto">
                          {entry.response_preview}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          ) : (
            <div className="text-center py-12 text-gray-400">
              暂无 Prompt 记录，请先运行任务
            </div>
          )}
        </div>
      )}

      {tab === 'log' && (
        <div className="p-4 bg-white border border-gray-200 rounded-lg">
          {logLines.length > 0 ? (
            <div className="space-y-1 font-mono text-xs text-gray-500 max-h-96 overflow-y-auto">
              {logLines.map((line, i) => (
                <div key={i} className="break-all">{line}</div>
              ))}
            </div>
          ) : (
            <div className="text-gray-400 text-center py-8">暂无日志</div>
          )}
        </div>
      )}
    </div>
  )
}
