import { useState } from 'react'
import { createInstructionPreset, updateInstructionPreset, deleteInstructionPreset } from '../../api/client'
import type { InstructionPreset } from '../../api/types'

export default function PresetModal({
  presets,
  onClose,
}: {
  presets: InstructionPreset[]
  onClose: () => void
}) {
  const [editing, setEditing] = useState<InstructionPreset | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editContent, setEditContent] = useState('')
  const [newName, setNewName] = useState('')
  const [newContent, setNewContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleCreate = async () => {
    if (!newName.trim() || !newContent.trim()) return
    setError(null)
    setSaving(true)
    try {
      await createInstructionPreset(newName.trim(), newContent.trim())
      setNewName('')
      setNewContent('')
      onClose()
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试")
    } finally {
      setSaving(false)
    }
  }

  const startEdit = (p: InstructionPreset) => {
    setEditing(p)
    setEditLabel(p.label)
    setEditContent(p.content)
  }

  const handleSaveEdit = async () => {
    if (!editing) return
    setError(null)
    setSaving(true)
    try {
      await updateInstructionPreset(editing.id, { label: editLabel.trim(), content: editContent.trim() })
      setEditing(null)
      onClose()
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试")
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (p: InstructionPreset) => {
    if (!confirm(`确定删除预设「${p.label}」？`)) return
    try {
      await deleteInstructionPreset(p.id)
      onClose()
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试")
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl max-h-[80vh] overflow-y-auto m-4" onClick={(e) => e.stopPropagation()}>
        {error && <div role="alert" className="m-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
        <div className="p-5 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900">管理预设指令</h2>
          <p className="text-xs text-gray-400 mt-1">保存常用的补充说明 prompt，点击标签即可快速添加</p>
        </div>

        {presets.length > 0 && (
          <div className="p-4 border-b border-gray-100">
            <h3 className="text-sm font-medium text-gray-700 mb-2">已保存的预设</h3>
            <div className="space-y-2">
              {presets.map((p) => (
                <div
                  key={p.id}
                  className={`p-3 border rounded-lg ${editing?.id === p.id ? 'border-blue-300 bg-blue-50' : 'border-gray-200'}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      {editing?.id === p.id ? (
                        <input
                          value={editLabel}
                          onChange={(e) => setEditLabel(e.target.value)}
                          className="w-full px-2 py-1 border border-gray-300 rounded text-sm mb-2"
                        />
                      ) : (
                        <p className="text-sm font-medium text-gray-900">{p.label}</p>
                      )}
                      <p className="text-xs text-gray-400 mt-0.5 truncate">{p.content_preview}...</p>
                    </div>
                    <div className="flex gap-1 shrink-0">
                      {editing?.id === p.id ? (
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
                            onClick={() => startEdit(p)}
                            className="text-xs px-2 py-1 bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
                          >
                            编辑
                          </button>
                          <button
                            onClick={() => handleDelete(p)}
                            className="text-xs px-2 py-1 bg-red-50 text-red-600 rounded hover:bg-red-100"
                          >
                            删除
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  {editing?.id === p.id && (
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      className="w-full mt-2 px-3 py-2 border border-gray-300 rounded text-sm resize-none"
                      rows={5}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-2">添加新预设</h3>
          <div className="space-y-3">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="预设名称，如 多模态融合、对比分析..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <textarea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              placeholder="补充说明的内容，如 重点关注多模态融合方法..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              rows={3}
            />
            <button
              onClick={handleCreate}
              disabled={saving || !newName.trim() || !newContent.trim()}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-100 disabled:text-gray-400 text-white rounded-lg text-sm font-medium transition-colors"
            >
              {saving ? '保存中...' : '保存预设'}
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
