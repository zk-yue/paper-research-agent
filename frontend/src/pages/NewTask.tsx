import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { expandKeywords, createTask } from '../api/client'
import type { CreateTask, SearchMode } from '../api/types'

interface KwHistoryEntry {
  id: string
  keywords: string[]
  createdAt: string
}

const STORAGE_KEY = 'kw_history'

function loadHistory(): KwHistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const value: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(value)
      ? value
          .filter(
            (entry): entry is KwHistoryEntry =>
              !!entry &&
              typeof entry.id === 'string' &&
              typeof entry.createdAt === 'string' &&
              Array.isArray(entry.keywords) &&
              entry.keywords.every(
                (keyword: unknown) => typeof keyword === 'string',
              ),
          )
          .slice(0, 20)
      : []
  } catch {
    return []
  }
}

function saveHistory(entries: KwHistoryEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch {
    /* History is optional when browser storage is unavailable. */
  }
}

const DEEP_STEPS = [
  { label: '主题理解', icon: '🧠' },
  { label: '多轮检索', icon: '🔍' },
  { label: '相关度过滤', icon: '📊' },
  { label: '综合总结', icon: '📚' },
  { label: '保存历史', icon: '💾' },
]

function dedupeKeywords(list: string[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const kw of list) {
    const trimmed = kw.trim()
    if (!trimmed) continue
    const key = trimmed.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    result.push(trimmed)
  }
  return result
}

function isWildcardKeyword(kw: string): boolean {
  return kw.includes('*')
}

function collectAllKeywords(keywords: string[], derived: string[][]): string[] {
  const combined: string[] = []
  for (let i = 0; i < keywords.length; i++) {
    const orig = keywords[i].trim()
    if (orig) combined.push(orig)
    if (derived[i]) combined.push(...derived[i])
  }
  return dedupeKeywords(combined)
}

interface RowExpandPicker {
  rowIdx: number
  options: string[]
  selected: Set<string>
}

export default function NewTask() {
  const navigate = useNavigate()
  const [searchMode, setSearchMode] = useState<SearchMode>('keyword')
  const [topic, setTopic] = useState('')
  const [deepRounds, setDeepRounds] = useState(3)
  const [yearFrom, setYearFrom] = useState(new Date().getFullYear() - 5)
  const [keywords, setKeywords] = useState<string[]>([''])
  const [derivedKeywords, setDerivedKeywords] = useState<string[][]>([[]])
  const [quickMode, setQuickMode] = useState(false)
  const [expandingIdx, setExpandingIdx] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [history, setHistory] = useState<KwHistoryEntry[]>(() => loadHistory())
  const [historyOpen, setHistoryOpen] = useState(false)
  const [rowExpandPicker, setRowExpandPicker] =
    useState<RowExpandPicker | null>(null)

  const expansionVersion = useRef(0)
  const submittingRef = useRef(false)
  const [error, setError] = useState('')
  const [expandNotice, setExpandNotice] = useState('')
  const invalidateExpansion = () => {
    expansionVersion.current += 1
    setExpandingIdx(null)
    setRowExpandPicker(null)
    setExpandNotice('')
  }

  const allKeywords = collectAllKeywords(keywords, derivedKeywords)

  const handleExpandKeyword = async (idx: number) => {
    const kw = keywords[idx].trim()
    if (!kw) return
    const version = ++expansionVersion.current
    setExpandNotice('')
    setExpandingIdx(idx)
    setRowExpandPicker(null)
    try {
      const res = await expandKeywords([kw])
      if (version !== expansionVersion.current) return
      const options = dedupeKeywords(res.keywords)
      const variants = options.filter(
        (k) => k.toLowerCase() !== kw.toLowerCase(),
      )
      if (variants.length === 0) {
        setExpandNotice('没有找到新的关键词变体，可以直接使用当前关键词。')
        return
      }
      setRowExpandPicker({
        rowIdx: idx,
        options: variants,
        selected: new Set(),
      })
    } catch (e) {
      if (version === expansionVersion.current)
        setExpandNotice(
          e instanceof Error ? e.message : '关键词扩展失败，请重试。',
        )
    } finally {
      if (version === expansionVersion.current) setExpandingIdx(null)
    }
  }

  const toggleExpandOption = (option: string) => {
    setRowExpandPicker((prev) => {
      if (!prev) return prev
      const next = new Set(prev.selected)
      if (next.has(option)) next.delete(option)
      else next.add(option)
      return { ...prev, selected: next }
    })
  }

  const selectAllExpandOptions = () => {
    setRowExpandPicker((prev) =>
      prev ? { ...prev, selected: new Set(prev.options) } : prev,
    )
  }

  const selectNoExpandOptions = () => {
    setRowExpandPicker((prev) =>
      prev ? { ...prev, selected: new Set() } : prev,
    )
  }

  const confirmRowExpandPicker = () => {
    if (!rowExpandPicker) return
    const selected = rowExpandPicker.options.filter((k) =>
      rowExpandPicker.selected.has(k),
    )
    if (selected.length === 0) return
    const { rowIdx } = rowExpandPicker
    setDerivedKeywords((prev) => {
      const next = [...prev]
      next[rowIdx] = dedupeKeywords([...(next[rowIdx] || []), ...selected])
      return next
    })
    setRowExpandPicker(null)
  }

  const cancelRowExpandPicker = () => setRowExpandPicker(null)

  const removeDerived = (rowIdx: number, variantIdx: number) => {
    setDerivedKeywords((prev) => {
      const next = [...prev]
      next[rowIdx] = next[rowIdx].filter((_, i) => i !== variantIdx)
      return next
    })
  }

  const addKeyword = () => {
    invalidateExpansion()
    setRowExpandPicker(null)
    setKeywords([...keywords, ''])
    setDerivedKeywords([...derivedKeywords, []])
  }

  const removeKeyword = (idx: number) => {
    invalidateExpansion()
    setRowExpandPicker(null)
    const nextKw = keywords.filter((_, i) => i !== idx)
    const nextDerived = derivedKeywords.filter((_, i) => i !== idx)
    setKeywords(nextKw.length > 0 ? nextKw : [''])
    setDerivedKeywords(nextDerived.length > 0 ? nextDerived : [[]])
  }

  const updateKeyword = (idx: number, val: string) => {
    invalidateExpansion()
    const next = [...keywords]
    next[idx] = val
    setKeywords(next)
    setRowExpandPicker((prev) => (prev?.rowIdx === idx ? null : prev))
    setDerivedKeywords((prev) => {
      const d = [...prev]
      d[idx] = []
      return d
    })
  }

  const selectHistory = (entry: KwHistoryEntry) => {
    invalidateExpansion()
    const kws = entry.keywords.length > 0 ? entry.keywords : ['']
    setRowExpandPicker(null)
    setKeywords(kws)
    setDerivedKeywords(kws.map(() => []))
    setHistoryOpen(false)
  }

  const deleteHistoryEntry = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    const next = history.filter((h) => h.id !== id)
    setHistory(next)
    saveHistory(next)
  }

  const handleSubmit = async () => {
    if (
      submittingRef.current ||
      !Number.isInteger(yearFrom) ||
      yearFrom < 1900 ||
      yearFrom > new Date().getFullYear() + 1
    )
      return
    if (searchMode === 'deep') {
      if (!topic.trim()) return
    } else if (allKeywords.length === 0) {
      return
    }

    submittingRef.current = true
    setError('')
    setSubmitting(true)
    try {
      const data: CreateTask =
        searchMode === 'deep'
          ? {
              year_from: yearFrom,
              enable_literature: true,
              quick_mode: quickMode,
              keywords: [],
              topic: topic.trim(),
              search_mode: 'deep',
              deep_rounds: deepRounds,
            }
          : {
              year_from: yearFrom,
              enable_literature: true,
              quick_mode: quickMode,
              keywords: allKeywords,
              search_mode: 'keyword',
            }
      const res = await createTask(data)

      const historyKeywords =
        searchMode === 'deep' ? [topic.trim()] : allKeywords
      const entry: KwHistoryEntry = {
        id: res.task_id,
        keywords: historyKeywords,
        createdAt: new Date().toISOString(),
      }
      const kwKey = historyKeywords.join(',')
      const next = [
        entry,
        ...history.filter((h) => h.keywords.join(',') !== kwKey),
      ].slice(0, 20)
      saveHistory(next)
      setHistory(next)

      navigate(`/task/${res.task_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败，请稍后重试。')
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  const previewSteps =
    searchMode === 'deep'
      ? DEEP_STEPS
      : [
          { label: '关键词确认', icon: '🔑' },
          { label: '论文搜索', icon: '🔍' },
          { label: '相关度过滤', icon: '📊' },
          { label: '文献调研', icon: '📚' },
          { label: '保存历史', icon: '💾' },
        ]

  const validYear =
    Number.isInteger(yearFrom) &&
    yearFrom >= 1900 &&
    yearFrom <= new Date().getFullYear() + 1
  const canSubmit =
    validYear &&
    (searchMode === 'deep' ? topic.trim().length > 0 : allKeywords.length > 0)

  return (
    <div>
      <p className="eyebrow">NEW RESEARCH</p>
      <h1 className="text-3xl font-semibold tracking-tight text-slate-900 mt-2">
        开启一项新的研究
      </h1>
      <p className="text-sm text-slate-500 mt-3 mb-8">
        从一个问题或一组关键词开始，收集论文、梳理证据，再形成你的研究综述。
      </p>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_280px] gap-6 items-start">
        <fieldset
          disabled={submitting}
          className="panel p-5 sm:p-7 space-y-7 min-w-0"
        >
          {/* Search mode */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              检索模式
            </label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => {
                  invalidateExpansion()
                  setSearchMode('keyword')
                }}
                aria-pressed={searchMode === 'keyword'}
                className={`p-3 border rounded-lg text-left transition-colors ${
                  searchMode === 'keyword'
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 bg-white hover:bg-gray-50'
                }`}
              >
                <p className="text-sm font-medium text-gray-900">关键词检索</p>
                <p className="text-xs text-gray-500 mt-1">
                  手动输入关键词，单次并行搜索
                </p>
              </button>
              <button
                type="button"
                onClick={() => {
                  invalidateExpansion()
                  setSearchMode('deep')
                }}
                aria-pressed={searchMode === 'deep'}
                className={`p-3 border rounded-lg text-left transition-colors ${
                  searchMode === 'deep'
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 bg-white hover:bg-gray-50'
                }`}
              >
                <p className="text-sm font-medium text-gray-900">
                  主题深度检索
                </p>
                <p className="text-xs text-gray-500 mt-1">
                  理解主题后多轮检索与总结
                </p>
              </button>
            </div>
          </div>

          {searchMode === 'deep' ? (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                研究主题
              </label>
              <textarea
                aria-label="研究主题"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="用自然语言描述你想调研的研究主题，例如：具身智能中基于触觉等变性的机器人操作策略"
                rows={4}
                className="w-full px-4 py-3 bg-white border border-gray-200 rounded-lg text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 resize-none"
              />
              <p className="text-xs text-gray-400 mt-1.5">
                Agent 将自动理解主题、生成检索策略，并进行多轮搜索与覆盖面优化
              </p>
              <div className="mt-4">
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  检索轮数
                </label>
                <input
                  type="number"
                  aria-label="检索轮数"
                  value={deepRounds}
                  onChange={(e) =>
                    setDeepRounds(
                      Math.min(5, Math.max(1, Number(e.target.value) || 3)),
                    )
                  }
                  min={1}
                  max={5}
                  className="w-32 px-3 py-2 bg-white border border-gray-200 rounded-lg text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500"
                />
                <span className="ml-2 text-xs text-gray-400">
                  1-5 轮，轮数越多覆盖越全面
                </span>
              </div>
            </div>
          ) : (
            <>
              {/* Keywords */}
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-sm font-medium text-gray-700">
                    搜索关键词
                  </label>
                  {history.length > 0 && (
                    <button
                      onClick={() => setHistoryOpen(!historyOpen)}
                      className="text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg transition-colors"
                    >
                      📋 历史记录 ({history.length})
                    </button>
                  )}
                </div>

                {/* History dropdown */}
                {historyOpen && history.length > 0 && (
                  <div className="mb-3 p-3 bg-white border border-gray-200 rounded-lg shadow-sm">
                    <p className="text-xs text-gray-400 mb-2">
                      点击使用历史关键词组合
                    </p>
                    <div className="space-y-1.5 max-h-48 overflow-y-auto">
                      {history.map((entry) => {
                        const timeStr = new Date(
                          entry.createdAt,
                        ).toLocaleString('zh-CN', {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                        return (
                          <div
                            key={entry.id}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => {
                              if (
                                e.target === e.currentTarget &&
                                (e.key === 'Enter' || e.key === ' ')
                              ) {
                                e.preventDefault()
                                selectHistory(entry)
                              }
                            }}
                            onClick={() => selectHistory(entry)}
                            className="p-2.5 bg-gray-50 hover:bg-gray-100 rounded-lg cursor-pointer transition-colors group"
                          >
                            <div className="flex items-start justify-between">
                              <p className="text-sm text-gray-900 truncate font-medium">
                                {entry.keywords.join(', ')}
                              </p>
                              <button
                                aria-label={`删除历史记录 ${entry.keywords.join(', ')}`}
                                onClick={(e) => deleteHistoryEntry(entry.id, e)}
                                className="text-gray-400 hover:text-red-500 ml-2 opacity-60 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                              >
                                ✕
                              </button>
                            </div>
                            <div className="flex flex-wrap gap-1 mt-1">
                              {entry.keywords.slice(0, 5).map((kw, i) => (
                                <span
                                  key={i}
                                  className="text-xs px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded"
                                >
                                  {kw}
                                </span>
                              ))}
                              {entry.keywords.length > 5 && (
                                <span className="text-xs text-gray-400">
                                  +{entry.keywords.length - 5}
                                </span>
                              )}
                            </div>
                            <span className="text-xs text-gray-400 mt-0.5 block">
                              {timeStr}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                <div className="space-y-3">
                  {keywords.map((kw, idx) => (
                    <div key={idx}>
                      <div className="flex gap-2 items-center">
                        <span className="text-xs text-gray-400 w-6 text-right shrink-0">
                          {idx + 1}
                        </span>
                        <input
                          type="text"
                          aria-label={`关键词 ${idx + 1}`}
                          value={kw}
                          onChange={(e) => updateKeyword(idx, e.target.value)}
                          placeholder="输入关键词，如 robot manipulation"
                          className="min-w-0 flex-1 px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500"
                        />
                        <button
                          onClick={() => handleExpandKeyword(idx)}
                          disabled={expandingIdx !== null || !kw.trim()}
                          className="text-xs px-2.5 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
                        >
                          {expandingIdx === idx ? '扩展中' : '🔑 扩展'}
                        </button>
                        {keywords.length > 1 && (
                          <button
                            aria-label={`删除关键词 ${idx + 1}`}
                            onClick={() => removeKeyword(idx)}
                            className="text-gray-400 hover:text-red-500 transition-colors p-1 shrink-0"
                          >
                            ✕
                          </button>
                        )}
                      </div>
                      {rowExpandPicker?.rowIdx === idx && (
                        <div className="mt-2 ml-8 p-3 bg-white border border-blue-200 rounded-lg shadow-sm">
                          <div className="flex items-center justify-between mb-2">
                            <p className="text-xs font-medium text-gray-700">
                              选择要保留的变体
                              <span className="ml-1.5 font-normal text-gray-400">
                                {rowExpandPicker.selected.size} /{' '}
                                {rowExpandPicker.options.length}
                              </span>
                            </p>
                            <div className="flex gap-1.5">
                              <button
                                onClick={selectAllExpandOptions}
                                className="text-xs px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded transition-colors"
                              >
                                全选
                              </button>
                              <button
                                onClick={selectNoExpandOptions}
                                className="text-xs px-2 py-0.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded transition-colors"
                              >
                                全不选
                              </button>
                            </div>
                          </div>
                          <p className="text-xs text-gray-400 mb-2">
                            默认不勾选；带「通配符」的项用于 arXiv 前缀匹配（如
                            tactile* 可匹配 visuotactile）
                          </p>
                          <div className="space-y-1 max-h-40 overflow-y-auto mb-2">
                            {rowExpandPicker.options.map((option) => {
                              const checked =
                                rowExpandPicker.selected.has(option)
                              const wildcard = isWildcardKeyword(option)
                              return (
                                <label
                                  key={option}
                                  className={`flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                                    checked
                                      ? 'bg-blue-50 hover:bg-blue-100/80'
                                      : 'bg-gray-50 hover:bg-gray-100'
                                  }`}
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => toggleExpandOption(option)}
                                    className="w-3.5 h-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                  />
                                  <span className="text-sm text-gray-900 flex-1">
                                    {option}
                                  </span>
                                  {wildcard && (
                                    <span className="text-xs px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded">
                                      通配符
                                    </span>
                                  )}
                                </label>
                              )
                            })}
                          </div>
                          <div className="flex justify-end gap-2">
                            <button
                              onClick={cancelRowExpandPicker}
                              className="text-xs px-2.5 py-1 text-gray-600 hover:text-gray-800 transition-colors"
                            >
                              取消
                            </button>
                            <button
                              onClick={confirmRowExpandPicker}
                              disabled={rowExpandPicker.selected.size === 0}
                              className="text-xs px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-100 disabled:text-gray-400 text-white rounded-lg transition-colors"
                            >
                              确认保留
                            </button>
                          </div>
                        </div>
                      )}
                      {derivedKeywords[idx]?.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mt-1.5 ml-8">
                          {derivedKeywords[idx].map((variant, vIdx) => (
                            <span
                              key={`${variant}-${vIdx}`}
                              className={`inline-flex items-center gap-1 text-xs px-2 py-1 border rounded-md ${
                                isWildcardKeyword(variant)
                                  ? 'bg-amber-50 text-amber-800 border-amber-200'
                                  : 'bg-blue-50 text-blue-700 border-blue-100'
                              }`}
                            >
                              {variant}
                              <button
                                onClick={() => removeDerived(idx, vIdx)}
                                className="text-blue-400 hover:text-red-500 transition-colors leading-none"
                                aria-label={`移除 ${variant}`}
                              >
                                ✕
                              </button>
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <button
                  onClick={addKeyword}
                  className="mt-2 text-sm text-blue-600 hover:text-blue-500 transition-colors"
                >
                  + 添加关键词
                </button>
              </div>
            </>
          )}

          {expandNotice && (
            <p
              role="status"
              className="text-sm rounded-lg bg-amber-50 text-amber-800 p-3"
            >
              {expandNotice}
            </p>
          )}
          {/* Year */}
          <div className="flex gap-4">
            <div className="flex-1">
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                检索起始年份
              </label>
              <input
                type="number"
                aria-label="检索起始年份"
                aria-invalid={!validYear}
                value={yearFrom}
                onChange={(e) => setYearFrom(Number(e.target.value))}
                min={1900}
                max={new Date().getFullYear() + 1}
                className="w-full px-4 py-2.5 bg-white border border-gray-200 rounded-lg text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500"
              />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={quickMode}
                  onChange={(e) => setQuickMode(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300 bg-white text-blue-600 focus:ring-blue-500"
                />
                <span className="text-sm text-gray-500">快速检索</span>
              </label>
            </div>
          </div>

          <p className="text-xs leading-relaxed text-slate-500">年份是硬性范围，较新的起始年份会排除早期代表工作。快速检索减少每条查询的数量并跳过摘要翻译和长篇总结，但保留全部通过筛选的论文。</p>

          {!validYear && (
            <p role="alert" className="text-sm text-red-600">
              请输入 1900 至 {new Date().getFullYear() + 1} 之间的年份。
            </p>
          )}
          {error && (
            <div role="alert" className="error-banner">
              {error}
            </div>
          )}
          <button
            onClick={handleSubmit}
            disabled={submitting || !canSubmit}
            className="btn-primary w-full justify-center py-3"
          >
            {submitting ? '正在创建研究…' : '开始研究 →'}
          </button>
          <p className="text-xs text-slate-400 text-center">
            任务开始后可离开本页，进度和结果会自动保存。
          </p>
        </fieldset>
        <aside className="panel p-6 lg:sticky lg:top-6">
          <p className="eyebrow">RESEARCH PLAN</p>
          <h2 className="font-semibold text-slate-900 mt-2 mb-5">
            本次研究计划
          </h2>
          <dl className="text-sm space-y-3 pb-5 border-b border-slate-100">
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">检索方式</dt>
              <dd>{searchMode === 'deep' ? '主题深度检索' : '关键词检索'}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">检索范围</dt>
              <dd>{validYear ? `${yearFrom} 年起` : '待填写'}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">
                {searchMode === 'deep' ? '检索轮数' : '关键词数量'}
              </dt>
              <dd>
                {searchMode === 'deep'
                  ? `${deepRounds} 轮`
                  : `${allKeywords.length} 个`}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">运行方式</dt>
              <dd>{quickMode ? '快速检索' : '完整研究'}</dd>
            </div>
          </dl>
          <ol className="space-y-4 mt-5">
            {previewSteps.map((step, i) => (
              <li
                key={step.label}
                className="flex items-center gap-3 text-sm text-slate-600"
              >
                <span className="rounded-full bg-slate-100 text-slate-500 w-6 h-6 inline-flex items-center justify-center text-xs">
                  {i + 1}
                </span>
                {step.label}
              </li>
            ))}
          </ol>
          <p className="text-xs text-slate-500 leading-relaxed mt-6 p-3 bg-slate-50 rounded-lg">
            {searchMode === 'deep'
              ? '适合探索尚不熟悉的方向。主题越具体，检索结果越聚焦。'
              : '适合已有明确术语的研究。可以先扩展同义词，再选择需要的变体。'}
          </p>
        </aside>
      </div>
    </div>
  )
}
