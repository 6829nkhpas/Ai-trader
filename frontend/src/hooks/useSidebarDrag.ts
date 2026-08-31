import { useState, useCallback } from 'react';

interface UseSidebarDragReturn {
  /** Current sidebar width (px). */
  sidebarWidth: number;
  /** Whether the user is currently resizing the sidebar. */
  isResizingSidebar: boolean;
  /** Attach to `onMouseDown` on the resize handle. */
  startResizingSidebar: (e: React.MouseEvent) => void;
}

/**
 * Encapsulates resizable-sidebar pointer logic previously inlined in `Home`.
 *
 * The collapsed rail used to be a floating, vertically-draggable toggle button
 * (`rightButtonTop` / `isDraggingRight` / `handleRightButtonMouseDown`) that the
 * user had to locate and click to reopen the sidebar. `RightSidebar` now renders
 * an always-visible collapsed rail (mirroring the left NavRail), so there is no
 * button position left to drag — only the resize handle on the expanded panel
 * remains.
 */
export function useSidebarDrag(): UseSidebarDragReturn {
  // ── Resizable sidebar width ──────────────────────────────────────────
  const [sidebarWidth, setSidebarWidth] = useState(300);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);

  const startResizingSidebar = useCallback(
    (mouseDownEvent: React.MouseEvent) => {
      mouseDownEvent.preventDefault();
      setIsResizingSidebar(true);

      const startWidth = sidebarWidth;
      const startX = mouseDownEvent.clientX;

      const doDrag = (mouseMoveEvent: MouseEvent) => {
        const deltaX = mouseMoveEvent.clientX - startX;
        const newWidth = Math.max(200, Math.min(600, startWidth - deltaX));
        setSidebarWidth(newWidth);
      };

      const stopDrag = () => {
        setIsResizingSidebar(false);
        document.removeEventListener('mousemove', doDrag);
        document.removeEventListener('mouseup', stopDrag);
      };

      document.addEventListener('mousemove', doDrag);
      document.addEventListener('mouseup', stopDrag);
    },
    [sidebarWidth],
  );

  return {
    sidebarWidth,
    isResizingSidebar,
    startResizingSidebar,
  };
}
