import { useState, useCallback } from 'react';

interface UseSidebarDragReturn {
  /** Vertical offset (px) for the collapsed-sidebar toggle button. */
  rightButtonTop: number;
  /** Whether the user is currently dragging the toggle button. */
  isDraggingRight: boolean;
  /** Attach to `onMouseDown` on the toggle button. */
  handleRightButtonMouseDown: (e: React.MouseEvent<HTMLButtonElement>) => void;
  /** Current sidebar width (px). */
  sidebarWidth: number;
  /** Whether the user is currently resizing the sidebar. */
  isResizingSidebar: boolean;
  /** Attach to `onMouseDown` on the resize handle. */
  startResizingSidebar: (e: React.MouseEvent) => void;
}

/**
 * Encapsulates draggable-toggle and resizable-sidebar pointer logic
 * previously inlined in `Home`.
 *
 * @param onToggleOpen Called (without args) when the user clicks the
 *   collapsed-sidebar toggle without dragging.
 */
export function useSidebarDrag(onToggleOpen: () => void): UseSidebarDragReturn {
  // ── Draggable collapsed-button ───────────────────────────────────────
  const [rightButtonTop, setRightButtonTop] = useState(8);
  const [isDraggingRight, setIsDraggingRight] = useState(false);

  const handleRightButtonMouseDown = useCallback(
    (e: React.MouseEvent<HTMLButtonElement>) => {
      e.preventDefault();
      const startY = e.clientY;
      const startTop = rightButtonTop;
      let dragged = false;

      const onMouseMove = (moveEvent: MouseEvent) => {
        const deltaY = moveEvent.clientY - startY;
        if (Math.abs(deltaY) > 4) { dragged = true; setIsDraggingRight(true); }
        const newTop = Math.max(8, Math.min(window.innerHeight - 80, startTop + deltaY));
        setRightButtonTop(newTop);
      };

      const onMouseUp = () => {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        setIsDraggingRight(false);
        if (!dragged) onToggleOpen();
      };

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    },
    [rightButtonTop, onToggleOpen],
  );

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
    rightButtonTop,
    isDraggingRight,
    handleRightButtonMouseDown,
    sidebarWidth,
    isResizingSidebar,
    startResizingSidebar,
  };
}
