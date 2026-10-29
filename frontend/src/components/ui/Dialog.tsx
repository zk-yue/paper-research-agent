import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import Icon from './Icon'

export default function Dialog({
  title,
  children,
  onClose,
  busy = false,
}: {
  title: string
  children: ReactNode
  onClose: () => void
  busy?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current!
    dialog.showModal()
    return () => dialog.close()
  }, [])
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="app-dialog"
      onCancel={(event) => {
        event.preventDefault()
        if (!busy) onClose()
      }}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect()
        if (
          !busy &&
          (event.clientX < bounds.left ||
            event.clientX > bounds.right ||
            event.clientY < bounds.top ||
            event.clientY > bounds.bottom)
        )
          onClose()
      }}
    >
      <div className="flex items-center justify-between gap-4 border-b border-gray-200 px-6 py-5">
        <h2 id={titleId} className="text-lg font-semibold">
          {title}
        </h2>
        <button
          className="icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label="关闭对话框"
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="p-6">{children}</div>
    </dialog>
  )
}
