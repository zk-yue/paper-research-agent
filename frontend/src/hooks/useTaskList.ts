import { useEffect, useState } from 'react'
import { fetchTaskSummaries } from '../api/client'
import type { TaskSummary } from '../api/types'

export function useTaskList() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    let controller: AbortController | null = null
    const load = async () => {
      if (document.hidden || disposed) return
      controller = new AbortController()
      let delay = 15000
      try {
        const data = await fetchTaskSummaries(controller.signal)
        if (disposed) return
        setTasks(data)
        setError('')
        setUpdatedAt(new Date())
        if (
          data.some(
            (task) => task.status === 'running' || task.status === 'pending',
          )
        )
          delay = 3000
      } catch (error) {
        if (!disposed && !controller.signal.aborted)
          setError(error instanceof Error ? error.message : '无法连接研究服务')
      } finally {
        controller = null
        if (!disposed) {
          setLoading(false)
          timer = setTimeout(load, delay)
        }
      }
    }
    const onVisibility = () => {
      clearTimeout(timer)
      if (!document.hidden && !controller) void load()
    }
    void load()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      disposed = true
      clearTimeout(timer)
      controller?.abort()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refreshKey])
  return {
    tasks,
    setTasks,
    loading,
    error,
    updatedAt,
    refresh: () => setRefreshKey((key) => key + 1),
  }
}
