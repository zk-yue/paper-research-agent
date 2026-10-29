const TASK_TABS = ['progress', 'papers', 'results', 'review', 'prompt', 'log'] as const

export type TaskTab = typeof TASK_TABS[number]

export function taskTabFromSearch(search: URLSearchParams): TaskTab {
  const value = search.get('tab')
  return TASK_TABS.find(tab => tab === value) ?? 'progress'
}

export function searchWithTaskTab(search: URLSearchParams, tab: TaskTab): URLSearchParams {
  const next = new URLSearchParams(search)
  next.set('tab', tab)
  return next
}
