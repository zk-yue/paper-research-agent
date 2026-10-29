import test from 'node:test'
import assert from 'node:assert/strict'
import { createMemoryRouter } from 'react-router-dom'
import { taskTabFromSearch, searchWithTaskTab } from '../src/lib/task-navigation.ts'

test('task links restore a whitelisted tab and safely default for unknown values', () => {
  assert.equal(taskTabFromSearch(new URLSearchParams('?tab=review')), 'review')
  for (const tab of ['progress', 'papers', 'results', 'prompt', 'log']) {
    assert.equal(taskTabFromSearch(new URLSearchParams({ tab })), tab)
  }
  for (const search of ['', '?tab=', '?tab=unknown', '?tab=Review', '?tab=__proto__']) {
    assert.equal(taskTabFromSearch(new URLSearchParams(search)), 'progress')
  }
})

test('tab changes preserve unrelated and repeated query parameters without mutation', () => {
  const original = new URLSearchParams('source=history&filter=first&filter=second&tab=papers')
  const updated = searchWithTaskTab(original, 'review')
  assert.equal(updated.get('source'), 'history')
  assert.deepEqual(updated.getAll('filter'), ['first', 'second'])
  assert.deepEqual(updated.getAll('tab'), ['review'])
  assert.equal(original.get('tab'), 'papers')
})

test('task tab navigation follows browser back and forward history', async () => {
  const router = createMemoryRouter(
    [{ path: '/' }, { path: '/task/:id' }],
    { initialEntries: ['/', '/task/example?source=history&tab=papers'] },
  )
  try {
    const currentTab = () => taskTabFromSearch(new URLSearchParams(router.state.location.search))
    await router.navigate({
      search: `?${searchWithTaskTab(new URLSearchParams(router.state.location.search), 'review')}`,
    })
    assert.equal(router.state.location.pathname, '/task/example')
    assert.equal(currentTab(), 'review')
    assert.equal(new URLSearchParams(router.state.location.search).get('source'), 'history')
    await router.navigate(-1)
    assert.equal(currentTab(), 'papers')
    await router.navigate(1)
    assert.equal(currentTab(), 'review')
    await router.navigate(-2)
    assert.equal(router.state.location.pathname, '/')
  } finally {
    router.dispose()
  }
})
