import type { ChartingLibraryWidgetOptions } from '../charting/datafeedTypes';
import { getThemeOverrides } from './tvThemeOverrides';
import { tvSaveLoadAdapter } from './tvSaveLoadAdapter';

interface WidgetOptionsInput {
  container: HTMLDivElement;
  datafeed: any;
  activeSymbol: string;
  resolution: string;
  theme: 'light' | 'dark';
}

/**
 * getTvWidgetOptions — helper to construct charting library widget config options.
 */
export function getTvWidgetOptions({
  container,
  datafeed,
  activeSymbol,
  resolution,
  theme,
}: WidgetOptionsInput): ChartingLibraryWidgetOptions {
  const sym = activeSymbol.toUpperCase();
  const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
  const exchange = isFno ? 'NFO' : 'NSE';

  return {
    container,
    datafeed,
    library_path: '/static/charting_library/charting_library/',
    symbol: `${exchange}:${activeSymbol}`,
    interval: resolution,
    timezone: 'Asia/Kolkata',
    theme: theme === 'light' ? 'light' : 'dark',
    locale: 'en',
    custom_css_url: '../../tvThemeOverrides.css',
    fullscreen: false,
    autosize: true,
    overrides: getThemeOverrides(theme),
    studies_overrides: {
      'volume.volume.color.0': '#ef4444',
      'volume.volume.color.1': '#10b981',
      'volume.volume.transparency': 50,
    },
    loading_screen: {
      backgroundColor: theme === 'light' ? '#f0eee9' : '#1e1e1e',
      foregroundColor: '#10b981',
    },
    disabled_features: [],
    enabled_features: [
      'use_localstorage_for_settings',
      'header_compare',
      'popup_hints',
      'load_last_chart',
      'study_templates',
      'side_toolbar_in_fullscreen_mode',
      'items_favoriting',
      'save_chart_properties_to_local_storage',
      'chart_style_hilo',
      'chart_style_range',
      'chart_style_renko',
      'chart_style_kagi',
      'chart_style_pnf',
      'chart_style_line_break',
      'chart_style_vol_footprint',
      'chart_style_tpo',
      'chart_style_svp',
      'chart_style_vol_candle',
      'display_market_status',
    ],
    debug: false,
    auto_save_delay: 5,
    save_load_adapter: tvSaveLoadAdapter,
    charts_storage_api_version: '1.1',
    client_id: 'ai-trader',
    user_id: 'local_user',
  };
}
