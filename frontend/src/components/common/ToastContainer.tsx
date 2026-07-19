'use client';

import React from 'react';

interface Toast {
  id: string;
  message: string;
  type: 'success' | 'info';
}

interface ToastContainerProps {
  toasts: Toast[];
}

/** Premium toast notifications — fixed bottom-right overlay. */
const ToastContainer: React.FC<ToastContainerProps> = ({ toasts }) => {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-3 max-w-sm pointer-events-none">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className="flex items-center gap-3 px-4 py-3.5 rounded-xl border border-emerald-500/20 bg-surface-elevated/80 backdrop-blur-xl shadow-2xl pointer-events-auto animate-slide-in-right"
          style={{
            background: 'linear-gradient(135deg, rgba(16, 185, 129, 0.08) 0%, rgba(5, 150, 105, 0.03) 100%)',
            backgroundColor: 'rgba(15, 23, 42, 0.85)',
            borderColor: 'rgba(16, 185, 129, 0.25)',
            boxShadow: '0 10px 30px -5px rgba(0, 0, 0, 0.5), 0 0 15px 0 rgba(16, 185, 129, 0.05)',
          }}
        >
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] font-black tracking-widest text-emerald-400 uppercase">SYSTEM NOTIFICATION</span>
            <span className="text-xs font-semibold text-white/90 leading-tight mt-0.5">{toast.message}</span>
          </div>
        </div>
      ))}
    </div>
  );
};

export default ToastContainer;
