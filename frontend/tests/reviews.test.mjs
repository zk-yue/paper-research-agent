import test from 'node:test'
import assert from 'node:assert/strict'
import { isReviewActive, reviewEvidenceSummary, reviewHeadings, reviewStatusLabel, selectReview, taskReviews } from '../src/lib/reviews.ts'

test('reopening a task restores the running review instead of stale completed content', () => {
  const reviews = [
    { id: 'older', status: 'done', created_at: '2026-10-28T12:00:00' },
    { id: 'running', status: 'running', created_at: '2026-10-29T12:00:00' },
    { id: 'latest', status: 'failed', created_at: '2026-10-29T13:00:00' },
  ]
  assert.equal(selectReview(reviews).id, 'running')
  assert.equal(selectReview(reviews, 'older').id, 'older')
  assert.equal(selectReview(reviews.filter(review => review.id !== 'running')).id, 'latest')
  assert.equal(reviewStatusLabel('failed'), '生成未完成')
  assert.equal(isReviewActive({ status: 'failed' }), false)
  assert.equal(isReviewActive({ status: 'interrupted' }), false)
  assert.equal(isReviewActive({ status: 'queued' }), true)
})

test('empty historical results and malformed entries do not break the review workspace', () => {
  assert.deepEqual(taskReviews({ results: null }), [])
  assert.deepEqual(taskReviews({ results: { custom_reviews: [null, 'draft', { id: 'saved', status: 'done' }] } }).map(review => review.id), ['saved'])
  assert.equal(selectReview([]), undefined)
})

test('table of contents links use original Markdown lines and ignore code examples', () => {
  const markdown = '# 总览\n\n## 方法比较\n```python\n## not a chapter\n```\n### **记忆机制**\n\n## 方法比较'
  assert.deepEqual(reviewHeadings(markdown), [
    { title: '总览', level: 1, line: 1 },
    { title: '方法比较', level: 2, line: 3 },
    { title: '记忆机制', level: 3, line: 7 },
    { title: '方法比较', level: 2, line: 9 },
  ])
})

test('completed review summary retains source limitations, citation coverage and quality warnings', () => {
  const summary = reviewEvidenceSummary({
    status: 'done',
    evidence_stats: { selected: 98, fulltext: 95, html: 94, pdf: 1, abstract: 2, metadata: 1 },
    coverage: { cited: 97, total: 98 },
    quality: { issues: ['Check comparison', 'Limited evidence'], invalid_citations: 1 },
  })
  assert.equal(summary.detail, '网页原文 94 篇 · PDF 原文 1 篇 · 摘要 2 篇 · 仅元信息 1 篇 · 正文引用 97/98 篇')
  assert.equal(summary.qualityIssueCount, 2)
  assert.equal(summary.invalidCitationCount, 1)
})

test('selecting an older review without evidence statistics does not claim a completed source audit', () => {
  const reviews = [{ id: 'old', status: 'done' }, { id: 'new', status: 'running' }]
  const legacy = selectReview(reviews, 'old')
  assert.deepEqual(reviewEvidenceSummary(legacy), {
    detail: '此版本未保存来源统计', qualityIssueCount: 0, invalidCitationCount: 0,
  })
  assert.equal(reviewEvidenceSummary({ evidence_stats: { fulltext: 2, abstract: 1, metadata: 0 } }).detail, '全文 2 篇 · 摘要 1 篇')
  assert.equal(reviewEvidenceSummary({ evidence_stats: { html: 2, abstract: 0, metadata: 0 } }).detail, '网页原文 2 篇')
})
