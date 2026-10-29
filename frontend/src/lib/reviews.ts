import type { CustomReview, TaskInfo } from '../api/types'

export function taskReviews(task: Pick<TaskInfo, 'results'>): CustomReview[] {
  const value = task.results?.custom_reviews
  return Array.isArray(value)
    ? value.filter((item): item is CustomReview => !!item && typeof item === 'object' && typeof item.id === 'string')
    : []
}

export function isReviewActive(review: Pick<CustomReview, 'status'>): boolean {
  return review.status === 'queued' || review.status === 'running'
}

export function reviewStatusLabel(status: string): string {
  return ({ queued: '等待开始', running: '生成中', done: '已完成', failed: '生成未完成', error: '生成未完成', cancelled: '已停止', interrupted: '已中断' } as Record<string, string>)[status] || status
}

export function selectReview(reviews: CustomReview[], selectedId?: string | null): CustomReview | undefined {
  const selected = selectedId ? reviews.find(review => review.id === selectedId) : undefined
  if (selected) return selected
  const recent = [...reviews].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
  return recent.find(isReviewActive) || recent[0]
}

export function reviewEvidenceSummary(review: CustomReview): {
  detail: string
  qualityIssueCount: number
  invalidCitationCount: number
} {
  const parts = []
  const evidence = review.evidence_stats
  if (evidence) {
    if (typeof evidence.html === 'number') parts.push(`网页原文 ${evidence.html} 篇`)
    if (typeof evidence.pdf === 'number') parts.push(`PDF 原文 ${evidence.pdf} 篇`)
    if (evidence.html === undefined && evidence.pdf === undefined) parts.push(`全文 ${evidence.fulltext} 篇`)
    if (evidence.abstract > 0) parts.push(`摘要 ${evidence.abstract} 篇`)
    if (evidence.metadata > 0) parts.push(`仅元信息 ${evidence.metadata} 篇`)
  }
  if (review.coverage) parts.push(`正文引用 ${review.coverage.cited}/${review.coverage.total} 篇`)
  return {
    detail: parts.join(' · ') || '此版本未保存来源统计',
    qualityIssueCount: review.quality?.issues?.length || 0,
    invalidCitationCount: review.quality?.invalid_citations || 0,
  }
}

export function reviewHeadings(markdown: string): Array<{ title: string; level: number; line: number }> {
  const headings = []
  let fence: string | null = null
  for (const [index, line] of markdown.split('\n').entries()) {
    const codeFence = line.match(/^\s*(`{3,}|~{3,})/)
    if (codeFence) {
      if (!fence) fence = codeFence[1][0]
      else if (codeFence[1][0] === fence) fence = null
      continue
    }
    if (fence) continue
    const match = line.match(/^(#{1,3})\s+(.+?)\s*#*$/)
    if (match) headings.push({ title: match[2].replace(/[*`]/g, ''), level: match[1].length, line: index + 1 })
  }
  return headings
}
