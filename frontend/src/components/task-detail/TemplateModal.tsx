import { useState } from 'react'
import { createReviewTemplate, updateReviewTemplate, deleteReviewTemplate, fetchReviewTemplate } from '../../api/client'
import type { ReviewTemplate } from '../../api/types'

export default function TemplateModal({
  templates,
  onClose,
}: {
  templates: ReviewTemplate[]
  onClose: () => void
}) {
  const [editing, setEditing] = useState<ReviewTemplate | null>(null)
  const [editJournalName, setEditJournalName] = useState('')
  const [editPapers, setEditPapers] = useState<Array<{ title: string; content: string }>>([])
  const [newName, setNewName] = useState('')
  const [newPapers, setNewPapers] = useState<Array<{ title: string; content: string }>>([{ title: '', content: '' }])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleCreate = async () => {
    if (!newName.trim() || newPapers.some(p => !p.content.trim())) return
    setError(null)
    setSaving(true)
    try {
      await createReviewTemplate(newName.trim(), newPapers.map(p => ({
        title: p.title.trim(),
        content: p.content.trim(),
      })))
      setNewName('')
      setNewPapers([{ title: '', content: '' }])
      onClose()
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试")
    } finally {
      setSaving(false)
    }
  }

  const startEdit = async (t: ReviewTemplate) => {
    setEditing(t)
    setEditJournalName(t.journal_name)
    // Fetch full template data (list endpoint only returns preview)
    try {
      const full = await fetchReviewTemplate(t.id)
      let papers: Array<{ title: string; content: string }> = []
      if (full.papers && full.papers.length > 0) {
        papers = full.papers
      } else if (full.content) {
        // backward compat: legacy single content field
        papers = [{ title: '', content: full.content }]
      }
      setEditPapers(papers.map(p => ({ title: p.title || '', content: p.content || '' })))
    } catch {
      // fallback to what we have
      const papers = t.papers && t.papers.length > 0 ? t.papers : [{ title: '', content: t.content || '' }]
      setEditPapers(papers.map(p => ({ title: p.title || '', content: p.content || '' })))
    }
  }

  const handleSaveEdit = async () => {
    if (!editing || !editJournalName.trim()) return
    setError(null)
    setSaving(true)
    try {
      await updateReviewTemplate(editing.id, {
        journal_name: editJournalName.trim(),
        papers: editPapers.map(p => ({ title: p.title.trim(), content: p.content.trim() })).filter(p => p.content),
      })
      setEditing(null)
      onClose()
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试")
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (t: ReviewTemplate) => {
    if (!confirm(`确定删除模板「${t.journal_name}」？`)) return
    try {
      await deleteReviewTemplate(t.id)
      onClose()
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试")
    }
  }

  const addPaperSlot = (isNew: boolean) => {
    if (isNew) {
      setNewPapers(prev => [...prev, { title: '', content: '' }])
    } else {
      setEditPapers(prev => [...prev, { title: '', content: '' }])
    }
  }

  const removePaperSlot = (isNew: boolean, idx: number) => {
    if (isNew) {
      setNewPapers(prev => prev.filter((_, i) => i !== idx))
    } else {
      setEditPapers(prev => prev.filter((_, i) => i !== idx))
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] overflow-y-auto m-4" onClick={(e) => e.stopPropagation()}>
        {error && <div role="alert" className="m-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
        <div className="p-5 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900">管理综述模板</h2>
          <p className="text-xs text-gray-400 mt-1">保存目标期刊 Related Works 范文作为写作风格参考，建议 2-3 篇</p>
        </div>

        {/* Existing templates */}
        {templates.length > 0 && (
          <div className="p-4 border-b border-gray-100">
            <h3 className="text-sm font-medium text-gray-700 mb-2">已保存的模板</h3>
            <div className="space-y-2">
              {templates.map((t) => (
                <div
                  key={t.id}
                  className={`p-3 border rounded-lg ${editing?.id === t.id ? 'border-blue-300 bg-blue-50' : 'border-gray-200'}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      {editing?.id === t.id ? (
                        <input
                          value={editJournalName}
                          onChange={(e) => setEditJournalName(e.target.value)}
                          className="w-full px-2 py-1 border border-gray-300 rounded text-sm"
                        />
                      ) : (
                        <p className="text-sm font-medium text-gray-900">{t.journal_name}</p>
                      )}
                      <p className="text-xs text-gray-400 mt-0.5">
                        {t.paper_count || (t.papers?.length) || 1} 篇范文 · {t.content_preview}...
                      </p>
                    </div>
                    <div className="flex gap-1 shrink-0">
                      {editing?.id === t.id ? (
                        <>
                          <button
                            onClick={handleSaveEdit}
                            disabled={saving}
                            className="text-xs px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-500"
                          >
                            保存
                          </button>
                          <button
                            onClick={() => setEditing(null)}
                            className="text-xs px-2 py-1 bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            onClick={() => startEdit(t)}
                            className="text-xs px-2 py-1 bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
                          >
                            编辑
                          </button>
                          <button
                            onClick={() => handleDelete(t)}
                            className="text-xs px-2 py-1 bg-red-50 text-red-600 rounded hover:bg-red-100"
                          >
                            删除
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  {editing?.id === t.id && (
                    <div className="mt-3 space-y-3">
                      {editPapers.map((p, idx) => (
                        <div key={idx} className="border border-gray-200 rounded-lg p-3 bg-white">
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-xs font-medium text-gray-500">范文 {idx + 1}</span>
                            {editPapers.length > 1 && (
                              <button
                                onClick={() => removePaperSlot(false, idx)}
                                className="text-xs text-red-500 hover:text-red-600"
                              >
                                删除
                              </button>
                            )}
                          </div>
                          <input
                            value={p.title}
                            onChange={(e) => {
                              const next = [...editPapers]
                              next[idx] = { ...next[idx], title: e.target.value }
                              setEditPapers(next)
                            }}
                            placeholder="论文标题（可选）"
                            className="w-full px-2 py-1 border border-gray-300 rounded text-sm mb-2"
                          />
                          <textarea
                            value={p.content}
                            onChange={(e) => {
                              const next = [...editPapers]
                              next[idx] = { ...next[idx], content: e.target.value }
                              setEditPapers(next)
                            }}
                            placeholder="粘贴 Related Works 原文..."
                            className="w-full px-3 py-2 border border-gray-300 rounded text-sm resize-none"
                            rows={5}
                          />
                        </div>
                      ))}
                      <button
                        onClick={() => addPaperSlot(false)}
                        className="text-sm text-blue-600 hover:text-blue-500 font-medium"
                      >
                        + 添加范文
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Add new template */}
        <div className="p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-2">添加新模板</h3>
          <div className="space-y-3">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="期刊名称，如 RA-L、ICRA、T-RO..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {newPapers.map((p, idx) => (
              <div key={idx} className="border border-gray-200 rounded-lg p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-gray-500">范文 {idx + 1}</span>
                  {newPapers.length > 1 && (
                    <button
                      onClick={() => removePaperSlot(true, idx)}
                      className="text-xs text-red-500 hover:text-red-600"
                    >
                      删除
                    </button>
                  )}
                </div>
                <input
                  value={p.title}
                  onChange={(e) => {
                    const next = [...newPapers]
                    next[idx] = { ...next[idx], title: e.target.value }
                    setNewPapers(next)
                  }}
                  placeholder="论文标题（可选）"
                  className="w-full px-2 py-1 border border-gray-300 rounded text-sm mb-2"
                />
                <textarea
                  value={p.content}
                  onChange={(e) => {
                    const next = [...newPapers]
                    next[idx] = { ...next[idx], content: e.target.value }
                    setNewPapers(next)
                  }}
                  placeholder="粘贴目标期刊 Related Works 的原文内容..."
                  className="w-full px-3 py-2 border border-gray-300 rounded text-sm resize-none"
                  rows={4}
                />
              </div>
            ))}
            <button
              onClick={() => addPaperSlot(true)}
              className="text-sm text-blue-600 hover:text-blue-500 font-medium"
            >
              + 添加范文
            </button>
            <button
              onClick={handleCreate}
              disabled={saving || !newName.trim() || newPapers.some(p => !p.content.trim())}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-100 disabled:text-gray-400 text-white rounded-lg text-sm font-medium transition-colors"
            >
              {saving ? '保存中...' : '保存模板'}
            </button>
          </div>
        </div>

        <div className="p-4 border-t border-gray-200">
          <button
            onClick={onClose}
            className="w-full py-2.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm font-medium transition-colors"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
