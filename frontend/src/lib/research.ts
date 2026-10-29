import type { PaperInfo, TaskSummary } from '../api/types'

export function taskTitle(task: { topic?: string | null; keywords: string[] }) {
  return task.topic?.trim() || task.keywords.join(' · ') || '未命名研究'
}
export function filterTasks(
  tasks: TaskSummary[],
  query: string,
  status: string,
  sort: string,
) {
  const term = query.trim().toLocaleLowerCase()
  return tasks
    .filter((task) => {
      const matchesStatus =
        status === 'all' ||
        (status === 'active'
          ? ['pending', 'running'].includes(task.status)
          : task.status === status)
      return (
        matchesStatus &&
        `${taskTitle(task)} ${task.keywords.join(' ')} ${task.id}`
          .toLocaleLowerCase()
          .includes(term)
      )
    })
    .sort((a, b) =>
      sort === 'papers'
        ? b.paper_count - a.paper_count
        : b.created_at.localeCompare(a.created_at),
    )
}
export function filterPapers(papers: PaperInfo[], query: string, sort: string) {
  const term = query.trim().toLocaleLowerCase()
  // Carry the original index through filtering/sorting: API selections use it.
  const rows = papers
    .map((paper, index) => ({ paper, index }))
    .filter(({ paper }) =>
      `${paper.title} ${paper.authors} ${paper.summary_cn || ''}`
        .toLocaleLowerCase()
        .includes(term),
    )
  if (sort === 'relevance')
    rows.sort(
      (a, b) =>
        (b.paper.relevance_score ?? -1) - (a.paper.relevance_score ?? -1),
    )
  if (sort === 'year')
    rows.sort(
      (a, b) => (Number(b.paper.year) || 0) - (Number(a.paper.year) || 0),
    )
  return rows
}
export function toggleVisibleSelection(
  selected: Set<number>,
  visible: number[],
): Set<number> {
  const next = new Set(selected)
  const allSelected =
    visible.length > 0 && visible.every((index) => selected.has(index))
  visible.forEach((index) => {
    if (allSelected) next.delete(index)
    else next.add(index)
  })
  return next
}
export function downloadCounts(results: Record<string, { success?: boolean }>) {
  const entries = Object.values(results)
  return {
    total: entries.length,
    success: entries.filter((result) => result.success).length,
    failed: entries.filter((result) => !result.success).length,
  }
}
