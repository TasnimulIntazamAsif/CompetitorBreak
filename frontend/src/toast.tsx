import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'

export type ToastKind = 'ok' | 'error' | ''

interface ToastApi {
  show: (msg: string, kind?: ToastKind) => void
}

const ToastContext = createContext<ToastApi>({ show: () => {} })

export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ msg: string; kind: ToastKind } | null>(null)

  const show = useCallback((msg: string, kind: ToastKind = '') => {
    setToast({ msg, kind })
    window.setTimeout(() => setToast(null), 3500)
  }, [])

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      {toast && <div className={'toast ' + toast.kind}>{toast.msg}</div>}
    </ToastContext.Provider>
  )
}
