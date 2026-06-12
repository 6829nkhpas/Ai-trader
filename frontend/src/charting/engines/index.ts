// Feature: professional-charting-suite
//
// Barrel for the pure charting engines. Engine modules (chartTypeEngine,
// indicatorEngine, footprintEngine, volumeProfileEngine, strategyEngine,
// drawingEngine, validation) are added by subsequent tasks and re-exported
// here so consumers import from a single `charting/engines` entry point.

export { validateNumeric, validateParams } from './validation';

export {
  canonicalCandles,
  applyLatestCandleUpdate,
} from './canonicalCandles';
export type {
  LatestCandleUpdateKind,
  LatestCandleUpdateResult,
} from './canonicalCandles';

export {
  buildFootprint,
  cumulativeDelta,
  detectImbalances,
  DEFAULT_IMBALANCE_RATIO,
  MIN_IMBALANCE_RATIO,
  MAX_IMBALANCE_RATIO,
} from './footprintEngine';
export type {
  FootprintCell,
  FootprintCandle,
  BuildFootprintOptions,
} from './footprintEngine';

export {
  INDICATOR_REGISTRY,
  registerIndicator,
  getIndicator,
  listIndicators,
  searchIndicators,
} from './indicatorEngine';
export type {
  OverlayId,
  OscillatorId,
  IndicatorId,
  IndicatorParams,
  IndicatorLine,
  IndicatorBand,
  IndicatorPlot,
  IndicatorDef,
} from './indicatorEngine';

export {
  TOOL_REGISTRY,
  MULTI_MIN_ANCHORS,
  FIB_RATIOS,
  isComplete,
  fibLevels,
  magnetSnap,
  pointToPixel,
  pixelToPoint,
  clearUnlocked,
} from './drawingEngine';
export type {
  DrawingCategory,
  ToolSpec,
  Pixel,
  CoordinateViewport,
} from './drawingEngine';

export {
  buildSeries,
  computeHeikinAshi,
  validateChartTypeParams,
  CHART_TYPES,
  CHART_TYPE_PARAM_SPEC,
  CHART_TYPE_PARAM_DEFAULTS,
} from './chartTypeEngine';
export type {
  ChartType,
  ChartTypeParams,
  RenderableSeries,
} from './chartTypeEngine';

export {
  buildProfile,
  valueArea,
  DEFAULT_PROFILE_ROWS,
  MIN_PROFILE_ROWS,
  MAX_PROFILE_ROWS,
  DEFAULT_VALUE_AREA_PERCENT,
  MIN_VALUE_AREA_PERCENT,
  MAX_VALUE_AREA_PERCENT,
} from './volumeProfileEngine';
export type {
  ProfileRange,
  ProfileRangeSpec,
  ProfileRow,
  VolumeProfile,
  BuildProfileOptions,
} from './volumeProfileEngine';

export {
  STRATEGY_REGISTRY,
  registerStrategy,
  getStrategy,
  listStrategies,
} from './strategyEngine';
export type {
  SignalKind,
  Signal,
  StrategyParams,
  StrategySummary,
  StrategyDef,
} from './strategyEngine';
