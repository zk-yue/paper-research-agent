import { useState } from 'react'
import { Link } from 'react-router-dom'
import { deleteTask, downloadPapersFromHistory } from '../api/client'
import type { TaskSummary } from '../api/types'
import { useTaskList } from '../hooks/useTaskList'
import { downloadCounts, filterTasks, taskTitle } from '../lib/research'
import Dialog from '../components/ui/Dialog'
import Icon from '../components/ui/Icon'
import StatusBadge from '../components/ui/StatusBadge'

function TaskCard({
  task,
  onDelete,
}: {
  task: TaskSummary
  onDelete: () => void
}) {
  const currentStep = task.steps.find((step) => step.status === 'running')
  const active = task.status === 'running' || task.status === 'pending'
  const done = task.steps.filter((step) =>
    ['done', 'skipped'].includes(step.status),
  ).length
  const progress = task.steps.length
    ? Math.round((done / task.steps.length) * 100)
    : 0
  return (
    <article className="panel group flex min-w-0 flex-col transition-shadow hover:shadow-md hover:shadow-gray-200/40">
      <div className="flex items-center justify-between px-5 pt-5">
        <span className="flex items-center gap-2 text-[10px] font-medium tracking-wide text-gray-400">
          <Icon
            name={task.search_mode === 'deep' ? 'spark' : 'search'}
            width={14}
            height={14}
          />
          {task.search_mode === 'deep' ? '主题深度检索' : '关键词检索'}
          {task.quick_mode && ' · 快速测试'}
        </span>
        <StatusBadge status={task.status} />
      </div>
      <Link to={`/task/${task.id}`} className="block flex-1 px-5 pb-4 pt-4">
        <h2 className="mb-2 line-clamp-2 min-h-12 text-[15px] font-semibold leading-6 text-gray-800 transition-colors group-hover:text-blue-700">
          {taskTitle(task)}
        </h2>
        <div className="mb-4 flex flex-wrap gap-1.5">
          {task.keywords.slice(0, 2).map((keyword) => (
            <span
              key={keyword}
              className="max-w-full truncate rounded bg-gray-50 px-2 py-1 text-[10px] text-gray-500"
            >
              {keyword}
            </span>
          ))}
          {task.keywords.length > 2 && (
            <span className="p-1 text-[10px] text-gray-400">
              +{task.keywords.length - 2}
            </span>
          )}
        </div>
        {active ? (
          <div>
            <div className="mb-2 flex justify-between text-[11px] text-blue-700">
              <span>{currentStep?.label || '准备开始'}</span>
              <span>{progress}%</span>
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-blue-50">
              <div
                className="h-full rounded-full bg-blue-500"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-5 text-xs text-gray-500">
            <span className="flex items-center gap-1.5">
              <Icon name="book" width={14} height={14} />
              <b className="font-semibold text-gray-700">
                {task.paper_count}
              </b>{' '}
              篇论文
            </span>
            <span>{task.review_count} 篇综述</span>
            <span className="ml-auto text-blue-600">
              <Icon name="arrow" width={16} height={16} />
            </span>
          </div>
        )}
        {task.error && (
          <p className="mt-3 line-clamp-2 text-xs text-red-600">{task.error}</p>
        )}
      </Link>
      <div className="flex items-center justify-between border-t border-gray-100 px-5 py-2.5">
        <span className="flex items-center gap-1.5 text-[10px] text-gray-400">
          <Icon name="clock" width={12} height={12} />
          {new Date(task.created_at).toLocaleString('zh-CN', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
        <button
          onClick={onDelete}
          className="icon-button hover:text-red-600"
          aria-label={`删除任务 ${taskTitle(task)}`}
        >
          <Icon name="trash" width={14} height={14} />
        </button>
      </div>
    </article>
  )
}

function HistoryDownloadDialog({
  tasks,
  onClose,
}: {
  tasks: TaskSummary[]
  onClose: () => void
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const available = tasks.filter((task) => task.paper_count > 0)
  const download = async () => {
    setBusy(true)
    setMessage('正在下载，完成后将显示实际成功数量…')
    try {
      const response = await downloadPapersFromHistory([...selected])
      let success = 0,
        failed = 0
      for (const result of Object.values(response.results)) {
        if ('error' in result && typeof result.error === 'string') {
          failed++
          continue
        }
        const counts = downloadCounts(
          result as Record<string, { success: boolean }>,
        )
        success += counts.success
        failed += counts.failed
      }
      setMessage(
        `下载完成：${success} 篇成功${failed ? `，${failed} 项失败，请在任务内检查 PDF 链接` : ''}。文件已保存在本地任务目录。`,
      )
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '下载失败')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog title="批量下载论文" onClose={onClose} busy={busy}>
      <p className="mb-4 text-xs leading-6 text-gray-500">
        选择研究任务，下载其中可获取的论文 PDF。已有文件会自动跳过。
      </p>
      <div className="max-h-72 space-y-2 overflow-auto">
        {available.map((task) => (
          <label
            key={task.id}
            className="flex cursor-pointer items-center gap-3 rounded-lg border border-gray-200 p-3 hover:bg-gray-50"
          >
            <input
              type="checkbox"
              disabled={busy}
              checked={selected.has(task.id)}
              onChange={() =>
                setSelected((previous) => {
                  const next = new Set(previous)
                  if (next.has(task.id)) next.delete(task.id)
                  else next.add(task.id)
                  return next
                })
              }
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm">{taskTitle(task)}</span>
              <span className="text-xs text-gray-400">
                {task.paper_count} 篇论文
              </span>
            </span>
          </label>
        ))}
        {!available.length && (
          <p className="py-8 text-center text-sm text-gray-500">
            还没有可下载的论文。
          </p>
        )}
      </div>
      {message && (
        <p
          role="status"
          className="mt-4 rounded-lg bg-blue-50 p-3 text-xs leading-6 text-blue-700"
        >
          {message}
        </p>
      )}
      <div className="mt-6 flex justify-end gap-2">
        <button className="btn-secondary" disabled={busy} onClick={onClose}>
          关闭
        </button>
        <button
          className="btn-primary"
          disabled={busy || !selected.size}
          onClick={download}
        >
          <Icon name="download" />
          {busy ? '正在下载…' : `下载 ${selected.size} 个任务的论文`}
        </button>
      </div>
    </Dialog>
  )
}

export default function Dashboard() {
  const { tasks, setTasks, loading, error, updatedAt, refresh } = useTaskList()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [sort, setSort] = useState('recent')
  const [page, setPage] = useState(1)
  const [downloadOpen, setDownloadOpen] = useState(false)
  const [deletingTask, setDeletingTask] = useState<TaskSummary | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const stats = {
    all: tasks.length,
    active: tasks.filter((task) => ['running', 'pending'].includes(task.status))
      .length,
    completed: tasks.filter((task) => task.status === 'completed').length,
    papers: tasks.reduce((sum, task) => sum + task.paper_count, 0),
  }
  const filtered = filterTasks(tasks, query, status, sort)
  const pages = Math.max(1, Math.ceil(filtered.length / 12))
  const currentPage = Math.min(page, pages)
  const visible = filtered.slice((currentPage - 1) * 12, currentPage * 12)
  const filters = [
    { key: 'all', label: '全部任务' },
    { key: 'active', label: '进行中' },
    { key: 'completed', label: '已完成' },
    { key: 'failed', label: '失败' },
    { key: 'cancelled', label: '已停止' },
  ]
  const removeTask = async () => {
    if (!deletingTask) return
    setDeleting(true)
    setDeleteError('')
    try {
      await deleteTask(deletingTask.id)
      setTasks((previous) =>
        previous.filter((task) => task.id !== deletingTask.id),
      )
      setDeletingTask(null)
      refresh()
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }
  return (
    <div>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="eyebrow">YOUR RESEARCH LIBRARY</p>
          <h1 className="page-title">研究，从这里继续。</h1>
          <p className="page-description">
            管理你的文献调研，连接论文、证据与新的思考。
          </p>
        </div>
        <Link to="/new" className="btn-primary">
          <Icon name="plus" />
          新建研究任务
        </Link>
      </div>
      <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          {
            label: '研究任务',
            value: stats.all,
            note: '每一次探索，都有记录',
            icon: 'grid' as const,
          },
          {
            label: '正在进行',
            value: stats.active,
            note: stats.active ? '任务在后台持续运行' : '准备好开启下一次探索',
            icon: 'clock' as const,
          },
          {
            label: '已完成',
            value: stats.completed,
            note: '随时回看研究结果',
            icon: 'check' as const,
          },
          {
            label: '已收录论文',
            value: stats.papers,
            note: '各任务论文数合计',
            icon: 'book' as const,
          },
        ].map((stat) => (
          <div className="panel p-4 lg:p-5" key={stat.label}>
            <div className="mb-3 flex items-center justify-between text-[11px] text-gray-500">
              <span>{stat.label}</span>
              <span className="text-blue-500">
                <Icon name={stat.icon} width={16} height={16} />
              </span>
            </div>
            <div className="text-3xl font-medium tabular-nums tracking-tight text-gray-800">
              {loading ? '—' : stat.value}
            </div>
            <p className="mt-2 text-[10px] text-gray-400">{stat.note}</p>
          </div>
        ))}
      </div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-base font-semibold text-gray-800">我的任务</h2>
          <span className="rounded-md bg-gray-100 px-2 py-0.5 text-[10px] text-gray-500">
            {tasks.length}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary !py-2"
            onClick={() => setDownloadOpen(true)}
          >
            <Icon name="download" width={14} height={14} />
            批量下载
          </button>
          <button
            className="icon-button"
            aria-label="刷新任务"
            title={
              updatedAt
                ? `上次更新 ${updatedAt.toLocaleTimeString()}`
                : '刷新任务'
            }
            onClick={refresh}
          >
            <Icon name="refresh" width={16} height={16} />
          </button>
        </div>
      </div>
      <div className="panel mb-5 flex flex-wrap items-center gap-3 p-3">
        <div className="flex max-w-full gap-1 overflow-x-auto">
          {filters.map((filter) => (
            <button
              key={filter.key}
              className="filter-tab"
              aria-pressed={status === filter.key}
              onClick={() => {
                setStatus(filter.key)
                setPage(1)
              }}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <div className="relative min-w-44 flex-1 lg:ml-auto">
          <Icon
            name="search"
            className="absolute left-3 top-3 text-gray-400"
            width={14}
            height={14}
          />
          <input
            aria-label="搜索研究任务"
            className="form-input !py-2 !pl-9"
            placeholder="搜索主题、关键词或任务 ID"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setPage(1)
            }}
          />
        </div>
        <select
          aria-label="任务排序"
          className="form-input !w-auto !py-2"
          value={sort}
          onChange={(event) => {
            setSort(event.target.value)
            setPage(1)
          }}
        >
          <option value="recent">最近创建</option>
          <option value="papers">论文数量</option>
        </select>
      </div>
      {error && (
        <div
          role="alert"
          className="error-banner mb-4 flex items-center justify-between gap-4"
        >
          <span>
            连接异常：{error}。
            {tasks.length
              ? '正在展示上次加载的任务。'
              : '请确认后端服务已启动。'}
          </span>
          <button onClick={refresh} className="shrink-0 underline">
            重新连接
          </button>
        </div>
      )}
      {loading ? (
        <div className="grid gap-4 xl:grid-cols-2" aria-label="正在加载任务">
          {[1, 2, 3, 4].map((index) => (
            <div key={index} className="panel h-48 animate-pulse p-5">
              <div className="mb-5 h-3 w-20 rounded bg-gray-100" />
              <div className="h-5 w-3/4 rounded bg-gray-100" />
              <div className="mt-10 h-3 w-1/2 rounded bg-gray-100" />
            </div>
          ))}
        </div>
      ) : !visible.length ? (
        <div className="panel empty-state">
          <Icon
            name="search"
            width={32}
            height={32}
            className="mx-auto text-blue-400"
          />
          <h2>
            {tasks.length
              ? '没有找到匹配的研究'
              : error
                ? '暂时无法加载任务'
                : '从一个研究问题开始'}
          </h2>
          <p>
            {tasks.length
              ? '试试其他关键词，或查看全部任务。'
              : '输入关键词或研究主题，建立你的第一份文献记录。'}
          </p>
          {tasks.length ? (
            <button
              className="btn-secondary"
              onClick={() => {
                setQuery('')
                setStatus('all')
              }}
            >
              清除筛选
            </button>
          ) : (
            <Link to="/new" className="btn-primary">
              新建研究任务
              <Icon name="arrow" />
            </Link>
          )}
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {visible.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onDelete={() => {
                setDeletingTask(task)
                setDeleteError('')
              }}
            />
          ))}
        </div>
      )}
      {!loading && filtered.length > 0 && (
        <div className="mt-6 flex items-center justify-between text-[11px] text-gray-400">
          <span>
            显示 {(currentPage - 1) * 12 + 1}–
            {Math.min(currentPage * 12, filtered.length)} 项，共{' '}
            {filtered.length} 项
          </span>
          <div className="flex items-center gap-3">
            <button
              className="btn-secondary !px-3 !py-1.5"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
            >
              上一页
            </button>
            <span>
              {currentPage} / {pages}
            </span>
            <button
              className="btn-secondary !px-3 !py-1.5"
              disabled={currentPage === pages}
              onClick={() => setPage(currentPage + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      )}
      {downloadOpen && (
        <HistoryDownloadDialog
          tasks={tasks}
          onClose={() => setDownloadOpen(false)}
        />
      )}
      {deletingTask && (
        <Dialog
          title="删除研究任务"
          onClose={() => setDeletingTask(null)}
          busy={deleting}
        >
          <p className="mb-3 text-sm font-medium">{taskTitle(deletingTask)}</p>
          <p className="text-xs leading-6 text-gray-500">
            将删除此任务的记录、已下载论文与证据卡片。此操作无法撤销。只想中断运行时，请在详情页选择“停止运行”。
          </p>
          {deleteError && (
            <div role="alert" className="error-banner mt-4">
              {deleteError}
            </div>
          )}
          <div className="mt-6 flex justify-end gap-2">
            <button
              className="btn-secondary"
              disabled={deleting}
              onClick={() => setDeletingTask(null)}
            >
              保留任务
            </button>
            <button
              className="btn-danger"
              disabled={deleting}
              onClick={removeTask}
            >
              {deleting ? '正在删除…' : '确认删除'}
            </button>
          </div>
        </Dialog>
      )}
    </div>
  )
}
