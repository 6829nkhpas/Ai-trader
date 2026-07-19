/**
 * tvSaveLoadAdapter.ts — localStorage-backed implementation of TradingView's
 * IExternalSaveLoadAdapter for study templates, chart templates, charts,
 * and drawing templates. Lets the `study_templates` feature work properly
 * with no server-side storage.
 *
 * Everything is namespaced under `tv.sl.*` keys so it can be cleared at once.
 */

import type {
  StudyTemplateData,
  StudyTemplateMetaInfo,
} from '../charting/datafeedTypes';

// ── localStorage keys ────────────────────────────────────────────────────────
const KEY = {
  charts: 'tv.sl.charts',
  studyTemplates: 'tv.sl.studyTemplates',
  chartTemplates: 'tv.sl.chartTemplates',
  drawingTemplates: 'tv.sl.drawingTemplates',
  lineTools: 'tv.sl.lineTools',
  chartContent: 'tv.sl.chartContent',
} as const;

// ── Local shim types for the TV API surface we use ───────────────────────────
// Mirror of TV's ChartMetaInfo / ChartData shapes — kept local so we don't
// need to import the full vendored .d.ts into the app's type graph.
export interface ChartMetaInfo {
  id: string | number;
  name: string;
  symbol: string;
  resolution: string;
  timestamp: number;
}

export interface ChartData {
  id?: string | number;
  name: string;
  symbol: string;
  resolution: string;
  content: string;
}

export interface ChartTemplateContent {
  content: string;
}

export interface ChartTemplate {
  name: string;
  content: string;
}

// ── JSON-safe localStorage helpers (no exceptions on quota / corrupt data) ──
function readJSON<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJSON(key: string, value: unknown): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Quota exceeded or storage disabled — silently drop; TV will surface UI errors.
  }
}

function nextId(): string {
  return `chart_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

// ── Adapter implementation ──────────────────────────────────────────────────
export const tvSaveLoadAdapter = {
  // ── Charts ─────────────────────────────────────────────────────────────────
  async getAllCharts(): Promise<ChartMetaInfo[]> {
    const charts = readJSON<ChartData[]>(KEY.charts, []);
    return charts
      .filter((c) => c && typeof c.name === 'string')
      .map((c, idx) => ({
        id: c.id ?? idx,
        name: c.name,
        symbol: c.symbol,
        resolution: c.resolution,
        timestamp: typeof (c as any).timestamp === 'number'
          ? (c as any).timestamp
          : Date.now(),
      }));
  },

  async removeChart(id: string | number): Promise<void> {
    const charts = readJSON<ChartData[]>(KEY.charts, []);
    const next = charts.filter((c) => c.id !== id);
    writeJSON(KEY.charts, next);
  },

  async saveChart(chartData: ChartData): Promise<string | number> {
    const charts = readJSON<ChartData[]>(KEY.charts, []);
    const id = chartData.id ?? nextId();
    const idx = charts.findIndex((c) => c.id === id);
    const record: ChartData = { ...chartData, id };
    if (idx >= 0) charts[idx] = record;
    else charts.push(record);
    writeJSON(KEY.charts, charts);
    return id;
  },

  async getChartContent(chartId: string | number): Promise<string> {
    const charts = readJSON<ChartData[]>(KEY.charts, []);
    const found = charts.find((c) => c.id === chartId);
    if (!found) throw new Error(`Chart ${chartId} not found`);
    return found.content;
  },

  // ── Study Templates ────────────────────────────────────────────────────────
  async getAllStudyTemplates(): Promise<StudyTemplateMetaInfo[]> {
    const items = readJSON<StudyTemplateData[]>(KEY.studyTemplates, []);
    return items.map((t) => ({ name: t.name }));
  },

  async removeStudyTemplate(info: StudyTemplateMetaInfo): Promise<void> {
    const items = readJSON<StudyTemplateData[]>(KEY.studyTemplates, []);
    const next = items.filter((t) => t.name !== info.name);
    writeJSON(KEY.studyTemplates, next);
  },

  async saveStudyTemplate(data: StudyTemplateData): Promise<void> {
    const items = readJSON<StudyTemplateData[]>(KEY.studyTemplates, []);
    const idx = items.findIndex((t) => t.name === data.name);
    if (idx >= 0) items[idx] = data;
    else items.push(data);
    writeJSON(KEY.studyTemplates, items);
  },

  async getStudyTemplateContent(info: StudyTemplateMetaInfo): Promise<string> {
    const items = readJSON<StudyTemplateData[]>(KEY.studyTemplates, []);
    const found = items.find((t) => t.name === info.name);
    if (!found) throw new Error(`Study template ${info.name} not found`);
    return found.content;
  },

  // ── Chart Templates ───────────────────────────────────────────────────────
  async getAllChartTemplates(): Promise<string[]> {
    const items = readJSON<Record<string, ChartTemplateContent>>(KEY.chartTemplates, {});
    return Object.keys(items);
  },

  async saveChartTemplate(name: string, content: ChartTemplateContent): Promise<void> {
    const items = readJSON<Record<string, ChartTemplateContent>>(KEY.chartTemplates, {});
    items[name] = content;
    writeJSON(KEY.chartTemplates, items);
  },

  async removeChartTemplate(name: string): Promise<void> {
    const items = readJSON<Record<string, ChartTemplateContent>>(KEY.chartTemplates, {});
    delete items[name];
    writeJSON(KEY.chartTemplates, items);
  },

  async getChartTemplateContent(name: string): Promise<ChartTemplate> {
    const items = readJSON<Record<string, ChartTemplateContent>>(KEY.chartTemplates, {});
    const found = items[name];
    if (!found) throw new Error(`Chart template ${name} not found`);
    return { name, content: found.content };
  },

  // ── Drawing Templates ─────────────────────────────────────────────────────
  async getDrawingTemplates(toolName: string): Promise<string[]> {
    const items = readJSON<Record<string, Record<string, string>>>(KEY.drawingTemplates, {});
    return Object.keys(items[toolName] ?? {});
  },

  async loadDrawingTemplate(toolName: string, templateName: string): Promise<string> {
    const items = readJSON<Record<string, Record<string, string>>>(KEY.drawingTemplates, {});
    const found = items[toolName]?.[templateName];
    if (!found) throw new Error(`Drawing template ${templateName} not found for ${toolName}`);
    return found;
  },

  async removeDrawingTemplate(toolName: string, templateName: string): Promise<void> {
    const items = readJSON<Record<string, Record<string, string>>>(KEY.drawingTemplates, {});
    if (items[toolName]) {
      delete items[toolName][templateName];
      if (Object.keys(items[toolName]).length === 0) delete items[toolName];
    }
    writeJSON(KEY.drawingTemplates, items);
  },

  async saveDrawingTemplate(toolName: string, templateName: string, content: string): Promise<void> {
    const items = readJSON<Record<string, Record<string, string>>>(KEY.drawingTemplates, {});
    if (!items[toolName]) items[toolName] = {};
    items[toolName][templateName] = content;
    writeJSON(KEY.drawingTemplates, items);
  },

  // ── Line tools / drawings ───────────────────────────────────────────────────
  async saveLineToolsAndGroups(): Promise<void> {
    // No-op — drawings are persisted via the chart layout itself.
  },

  async loadLineToolsAndGroups(): Promise<null> {
    return null;
  },
};
