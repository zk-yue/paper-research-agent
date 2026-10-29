import test from 'node:test'
import assert from 'node:assert/strict'
import { filterPapers, filterTasks, toggleVisibleSelection, downloadCounts, taskTitle } from '../src/lib/research.ts'

const papers = [
  { title: 'Robot sensing', authors: 'Wang', year: '2024', relevance_score: 9 },
  { title: 'Touch', authors: 'Lee', year: '2026', relevance_score: 7, summary_cn: '机器人触觉' },
  { title: 'Robot manipulation', authors: 'Chen', year: '2025', relevance_score: 10 },
]
test('paper filtering and sorting retain API indices without mutating source', () => {
  assert.deepEqual(filterPapers(papers, 'ROBOT', 'relevance').map(row => row.index), [2, 0])
  assert.deepEqual(filterPapers(papers, '机器人', 'year').map(row => row.index), [1])
  assert.deepEqual(filterPapers(papers, 'wang', 'original').map(row => row.index), [0])
  assert.deepEqual(filterPapers(papers, '', 'year').map(row => row.index), [1, 2, 0])
  assert.equal(papers[0].title, 'Robot sensing')
})
test('select visible rows preserves hidden selections and toggles only visible rows', () => {
  const original = new Set([1])
  const selected = toggleVisibleSelection(original, [0, 2])
  assert.deepEqual([...selected], [1, 0, 2])
  assert.deepEqual([...toggleVisibleSelection(selected, [0, 2])], [1])
  assert.deepEqual([...toggleVisibleSelection(selected, [])], [1, 0, 2])
  assert.deepEqual([...original], [1])
})
test('task filters combine status and case insensitive terms', () => {
  const tasks = [
    { id: 'a', keywords: ['Touch'], status: 'running', created_at: '2026-01-01', paper_count: 1 },
    { id: 'b', keywords: ['Touch'], topic: 'Robot', status: 'completed', created_at: '2026-02-01', paper_count: 4 },
    { id: 'c', keywords: ['Vision'], status: 'pending', created_at: '2026-03-01', paper_count: 0 },
  ]
  assert.deepEqual(filterTasks(tasks, 'touch', 'active', 'recent').map(t => t.id), ['a'])
  assert.deepEqual(filterTasks(tasks, '', 'all', 'papers').map(t => t.id), ['b', 'a', 'c'])
  assert.deepEqual(filterTasks(tasks, 'robot', 'completed', 'recent').map(t => t.id), ['b'])
  assert.equal(taskTitle({ keywords: [] }), '未命名研究')
})
test('download summary counts failures instead of assuming HTTP success means all PDFs succeeded', () => {
  assert.deepEqual(downloadCounts({ a: { success: true }, b: { success: false }, c: {} }), { total: 3, success: 1, failed: 2 })
  assert.deepEqual(downloadCounts({}), { total: 0, success: 0, failed: 0 })
})
