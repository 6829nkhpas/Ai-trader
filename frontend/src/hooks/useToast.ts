import { useState, useCallback } from 'react';

interface Toast {
  id: string;
  message: string;
  type: 'success' | 'info';
}

interface UseToastReturn {
  toasts: Toast[];
  showToast: (message: string, type?: 'success' | 'info') => void;
}

/**
 * Lightweight toast-notification state extracted from `Home`.
 * Returns the active toasts and a `showToast` helper that auto-expires
 * each toast after 4.5 s.
 */
export function useToast(): UseToastReturn {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const showToast = useCallback((message: string, type: 'success' | 'info' = 'success') => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4500);
  }, []);

  return { toasts, showToast };
}
