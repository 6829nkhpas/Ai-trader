import { create } from 'zustand';

type CursorMode = 'cross' | 'dot' | 'arrow' | 'eraser';
type MagnetMode = 'off' | 'weak' | 'strong';

export type Point = { time: number; price: number };
export type Drawing = { id: string; tool: string; points: Point[] };

interface ChartUIState {
  activeCursor: CursorMode;
  activeDrawingTool: string | null;
  magnetMode: MagnetMode;
  drawingsVisible: boolean;
  drawingsLocked: boolean;
  drawings: Drawing[];

  setActiveCursor: (cursor: CursorMode) => void;
  setActiveDrawingTool: (tool: string | null) => void;
  setMagnetMode: (mode: MagnetMode) => void;
  toggleDrawingsVisible: () => void;
  toggleDrawingsLocked: () => void;
  addDrawing: (drawing: Drawing) => void;
  clearDrawings: () => void;
}

export const useChartUIStore = create<ChartUIState>((set) => ({
  activeCursor: 'cross',
  activeDrawingTool: null,
  magnetMode: 'off',
  drawingsVisible: true,
  drawingsLocked: false,
  drawings: [],

  setActiveCursor: (cursor) => set({ activeCursor: cursor, activeDrawingTool: null }),
  setActiveDrawingTool: (tool) => set({ activeDrawingTool: tool }),
  setMagnetMode: (mode) => set({ magnetMode: mode }),
  toggleDrawingsVisible: () => set((state) => ({ drawingsVisible: !state.drawingsVisible })),
  toggleDrawingsLocked: () => set((state) => ({ drawingsLocked: !state.drawingsLocked })),
  addDrawing: (drawing) =>
    set((state) => ({ drawings: [...state.drawings, drawing] })),
  clearDrawings: () => set({ drawings: [] }),
}));
