'use client';

import { useEffect, useRef } from 'react';

/**
 * Returns a ref to attach to a container element. Calls `onClose` whenever
 * a mousedown event occurs outside that container.
 */
export function useOutsideClose<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);
  return ref;
}
