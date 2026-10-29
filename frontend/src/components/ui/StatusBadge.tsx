const statusLabels: Record<string, string> = {
  completed: '已完成',
  running: '进行中',
  pending: '等待中',
  failed: '失败',
  cancelled: '已停止',
}
export default function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {statusLabels[status] || status}
    </span>
  )
}
