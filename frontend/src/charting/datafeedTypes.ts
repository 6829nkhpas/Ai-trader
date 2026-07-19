/**
 * charting/datafeedTypes.ts — TradingView Advanced Charts JS API type declarations.
 *
 * These types define the contract between our custom datafeed adapter and the
 * TradingView widget. They mirror the official Charting Library TypeScript
 * definitions but are kept minimal to avoid depending on the full `@types`
 * package.
 */

// ── Resolution ────────────────────────────────────────────────────────────
/** TV resolution string: minutes ('1','5','15','60'), 'D', 'W', 'M'. */
export type ResolutionString = string;

// ── Symbol Info ───────────────────────────────────────────────────────────
export interface LibrarySymbolInfo {
  name: string;
  full_name: string;
  ticker?: string;
  description: string;
  type: string;
  session: string;
  timezone: string;
  exchange: string;
  listed_exchange: string;
  format: 'price' | 'volume';
  minmov: number;
  pricescale: number;
  has_intraday: boolean;
  has_daily: boolean;
  has_weekly_and_monthly: boolean;
  supported_resolutions: ResolutionString[];
  intraday_multipliers?: string[];
  daily_multipliers?: string[];
  weekly_multipliers?: string[];
  monthly_multipliers?: string[];
  volume_precision: number;
  data_status: 'streaming' | 'endofday' | 'pulsed' | 'delayed_streaming';
  currency_code?: string;
}

// ── Bar ───────────────────────────────────────────────────────────────────
export interface Bar {
  time: number;   // UTC milliseconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

// ── Period Params (getBars) ───────────────────────────────────────────────
export interface PeriodParams {
  from: number;     // UNIX seconds
  to: number;       // UNIX seconds
  countBack: number;
  firstDataRequest: boolean;
}

// ── Search Symbol Result ─────────────────────────────────────────────────
export interface SearchSymbolResultItem {
  symbol: string;
  full_name: string;
  description: string;
  exchange: string;
  ticker: string;
  type: string;
}

// ── DatafeedConfiguration ────────────────────────────────────────────────
export interface DatafeedConfiguration {
  exchanges?: { value: string; name: string; desc: string }[];
  symbols_types?: { name: string; value: string }[];
  supported_resolutions?: ResolutionString[];
  supports_marks?: boolean;
  supports_timescale_marks?: boolean;
  supports_time?: boolean;
}

// ── Subscriber callbacks ─────────────────────────────────────────────────
export type OnReadyCallback = (configuration: DatafeedConfiguration) => void;
export type ResolveCallback = (symbolInfo: LibrarySymbolInfo) => void;
export type ErrorCallback = (reason: string) => void;
export type HistoryCallback = (bars: Bar[], meta: { noData?: boolean; nextTime?: number }) => void;
export type SubscribeBarsCallback = (bar: Bar) => void;
export type SearchSymbolsCallback = (items: SearchSymbolResultItem[]) => void;

// ── IBasicDatafeed ───────────────────────────────────────────────────────
export interface IBasicDatafeed {
  onReady: (callback: OnReadyCallback) => void;
  searchSymbols: (
    userInput: string,
    exchange: string,
    symbolType: string,
    onResult: SearchSymbolsCallback,
  ) => void;
  resolveSymbol: (
    symbolName: string,
    onResolve: ResolveCallback,
    onError: ErrorCallback,
  ) => void;
  getBars: (
    symbolInfo: LibrarySymbolInfo,
    resolution: ResolutionString,
    periodParams: PeriodParams,
    onResult: HistoryCallback,
    onError: ErrorCallback,
  ) => void;
  subscribeBars: (
    symbolInfo: LibrarySymbolInfo,
    resolution: ResolutionString,
    onTick: SubscribeBarsCallback,
    listenerGuid: string,
    onResetCacheNeededCallback: () => void,
  ) => void;
  unsubscribeBars: (listenerGuid: string) => void;
}

// ── Save / Load adapter types (subset of IExternalSaveLoadAdapter) ─────────
export interface StudyTemplateData {
  name: string;
  content: string;
}
export interface StudyTemplateMetaInfo {
  name: string;
}
export interface ChartMetaInfo {
  id: string | number;
  name: string;
  symbol: string;
  resolution: ResolutionString;
  timestamp: number;
}
export interface ChartData {
  id?: string | number;
  name: string;
  symbol: string;
  resolution: ResolutionString;
  content: string;
}
export interface SaveLoadAdapter {
  getAllCharts(): Promise<ChartMetaInfo[]>;
  removeChart(id: string | number): Promise<void>;
  saveChart(chartData: ChartData): Promise<string | number>;
  getChartContent(chartId: string | number): Promise<string>;
  getAllStudyTemplates(): Promise<StudyTemplateMetaInfo[]>;
  removeStudyTemplate(studyTemplateInfo: StudyTemplateMetaInfo): Promise<void>;
  saveStudyTemplate(studyTemplateData: StudyTemplateData): Promise<void>;
  getStudyTemplateContent(studyTemplateInfo: StudyTemplateMetaInfo): Promise<string>;
  getDrawingTemplates?(toolName: string): Promise<string[]>;
  loadDrawingTemplate?(toolName: string, templateName: string): Promise<string>;
  removeDrawingTemplate?(toolName: string, templateName: string): Promise<void>;
  saveDrawingTemplate?(toolName: string, templateName: string, content: string): Promise<void>;
  getAllChartTemplates?(): Promise<string[]>;
  saveChartTemplate?(name: string, content: unknown): Promise<void>;
  removeChartTemplate?(name: string): Promise<void>;
  getChartTemplateContent?(name: string): Promise<unknown>;
  saveLineToolsAndGroups?(...args: unknown[]): Promise<void>;
  loadLineToolsAndGroups?(...args: unknown[]): Promise<unknown>;
}

// ── Widget Constructor Options (subset) ──────────────────────────────────
export interface ChartingLibraryWidgetOptions {
  container: HTMLElement;
  datafeed: IBasicDatafeed;
  library_path: string;
  symbol: string;
  interval: ResolutionString;
  timezone?: string;
  theme?: 'Light' | 'Dark' | 'light' | 'dark';
  locale?: string;
  custom_css_url?: string;
  fullscreen?: boolean;
  autosize?: boolean;
  width?: number | string;
  height?: number | string;
  disabled_features?: string[];
  enabled_features?: string[];
  overrides?: Record<string, string | number | boolean>;
  studies_overrides?: Record<string, string | number | boolean>;
  debug?: boolean;
  auto_save_delay?: number;
  loading_screen?: { backgroundColor?: string; foregroundColor?: string };
  save_load_adapter?: SaveLoadAdapter;
  charts_storage_url?: string;
  charts_storage_api_version?: '1.0' | '1.1';
  client_id?: string;
  user_id?: string;
}

// ── IChartingLibraryWidget (subset of widget API) ────────────────────────
export interface IChartingLibraryWidget {
  onChartReady: (callback: () => void) => void;
  setSymbol: (symbol: string, interval: ResolutionString, callback?: () => void) => void;
  activeChart: () => IChartWidgetApi;
  remove: () => void;
  headerReady: () => Promise<void>;
  applyOverrides: (overrides: Record<string, string | number | boolean>) => void;
  changeTheme: (theme: 'Light' | 'Dark' | 'light' | 'dark') => void;
  save: (callback: (state: object) => void) => void;
  load: (state: object) => void;
}

// ── IChartWidgetApi (subset) ─────────────────────────────────────────────
export interface IChartWidgetApi {
  setResolution: (resolution: ResolutionString, callback?: () => void) => void;
  setChartType: (type: number) => void;
  setSymbol: (symbol: string, callback?: () => void) => void;
  getVisibleRange: () => { from: number; to: number };
  resetData: () => void;
}

// ── Global TradingView namespace (attached to window) ────────────────────
export interface TradingViewNamespace {
  widget: new (options: ChartingLibraryWidgetOptions) => IChartingLibraryWidget;
}

declare global {
  interface Window {
    TradingView?: TradingViewNamespace;
  }
}
