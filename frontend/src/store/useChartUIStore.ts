import { create } from 'zustand';

type CursorMode = 'cross' | 'dot' | 'arrow' | 'eraser';
type MagnetMode = 'off' | 'weak' | 'strong';

export type Point = { time: number; price: number };
export type Drawing = { id: string; tool: string; points: Point[]; color?: string; text?: string };

interface ChartUIState {
  activeCursor: CursorMode;
  activeDrawingTool: string | null;
  magnetMode: MagnetMode;
  drawingsVisible: boolean;
  drawingsLocked: boolean;
  drawings: Drawing[];
  selectedDrawingId: string | null;
  drawingColor: string;

  setActiveCursor: (cursor: CursorMode) => void;
  setActiveDrawingTool: (tool: string | null) => void;
  setMagnetMode: (mode: MagnetMode) => void;
  toggleDrawingsVisible: () => void;
  toggleDrawingsLocked: () => void;
  addDrawing: (drawing: Drawing) => void;
  updateDrawing: (id: string, updates: Partial<Drawing>) => void;
  updateDrawingPoints: (id: string, points: Point[]) => void;
  removeDrawing: (id: string) => void;
  setSelectedDrawing: (id: string | null) => void;
  clearDrawings: () => void;
  setDrawingColor: (color: string) => void;
}

export const useChartUIStore = create<ChartUIState>((set) => ({
  activeCursor: 'cross',
  activeDrawingTool: null,
  magnetMode: 'off',
  drawingsVisible: true,
  drawingsLocked: false,
  drawings: [],
  selectedDrawingId: null,
  drawingColor: '#FF5722',

  setActiveCursor: (cursor) => set({ activeCursor: cursor, activeDrawingTool: null }),
  setActiveDrawingTool: (tool) => set({ activeDrawingTool: tool, selectedDrawingId: null }),
  setMagnetMode: (mode) => set({ magnetMode: mode }),
  toggleDrawingsVisible: () => set((state) => ({ drawingsVisible: !state.drawingsVisible })),
  toggleDrawingsLocked: () => set((state) => ({ drawingsLocked: !state.drawingsLocked })),
  addDrawing: (drawing) =>
    set((state) => ({ drawings: [...state.drawings, drawing] })),
  updateDrawing: (id, updates) => set((state) => ({
    drawings: state.drawings.map((d) => (d.id === id ? { ...d, ...updates } : d))
  })),
  updateDrawingPoints: (id, points) =>
    set((state) => ({
      drawings: state.drawings.map((d) => (d.id === id ? { ...d, points } : d)),
    })),
  removeDrawing: (id) =>
    set((state) => ({
      drawings: state.drawings.filter((d) => d.id !== id),
      selectedDrawingId: state.selectedDrawingId === id ? null : state.selectedDrawingId,
    })),
  setSelectedDrawing: (id) => set({ selectedDrawingId: id }),
  clearDrawings: () => set({ drawings: [], selectedDrawingId: null }),
  setDrawingColor: (color) => set({ drawingColor: color }),
}));
