/**
 * Safe, non-deprecated "run this once the chart is ready" helper.
 *
 * Two problems it solves, both reported as bugs:
 *
 * 1. DEPRECATION — `IChartingLibraryWidget.onChartReady(cb)` is marked
 *    `@deprecated Use chartReady instead` in the vendored
 *    `charting_library.d.ts`. The replacement is the promise-returning
 *    `chartReady()`. We prefer it when present and fall back to the callback
 *    form so an older bundled library keeps working.
 *
 * 2. TEAR-DOWN RACE — the call itself was unguarded at every site. When the
 *    widget has already been `remove()`d, TradingView's internal iframe/API
 *    state is gutted and *invoking* `onChartReady` throws a TypeError before
 *    any of the guards inside the callback get a chance to run. That is the
 *    "GhostLine crashes with TypeError when switching symbol" report. Both the
 *    invocation and the callback body are wrapped here, so a dead widget is a
 *    no-op instead of an exception.
 *
 * The callback is also skipped entirely if `isCancelled()` reports true by the
 * time the chart becomes ready, so an effect that has already been cleaned up
 * never touches the chart.
 */

/** Minimal shape we need; the real widget type has far more surface. */
interface ReadyableWidget {
  chartReady?: () => Promise<void>;
  onChartReady?: (cb: () => void) => void;
  activeChart?: unknown;
}

/**
 * Run `fn` once the widget's chart is ready.
 *
 * @param widget      The TradingView widget (may be null or already removed).
 * @param fn          Work to run against the ready chart. May be async; a
 *                    rejection is caught and logged rather than becoming an
 *                    unhandled rejection.
 * @param isCancelled Optional staleness probe, checked again immediately
 *                    before `fn` runs.
 * @param label       Log prefix, so a warning names the feature that raced.
 */
export function whenChartReady(
  widget: unknown,
  fn: () => void | Promise<void>,
  isCancelled: () => boolean = () => false,
  label = 'Chart',
): void {
  if (!widget) return;
  const w = widget as ReadyableWidget;

  const run = () => {
    // The widget can be torn down between scheduling and running.
    if (isCancelled()) return;
    if (!w.activeChart) return;
    try {
      const result = fn();
      if (result && typeof (result as Promise<void>).catch === 'function') {
        (result as Promise<void>).catch((err) => {
          console.warn(`[${label}] chart-ready task failed:`, err);
        });
      }
    } catch (err) {
      console.warn(`[${label}] chart-ready task threw:`, err);
    }
  };

  try {
    if (typeof w.chartReady === 'function') {
      // Non-deprecated promise form.
      w.chartReady().then(run).catch(() => {
        // Widget removed before the chart settled — nothing to do.
      });
      return;
    }
    if (typeof w.onChartReady === 'function') {
      w.onChartReady(run);
    }
  } catch (err) {
    // `remove()` already ran: the internal API is gone. Not an error worth
    // surfacing to the user, but do not let it escape as a TypeError.
    console.warn(`[${label}] widget was torn down before chart-ready:`, err);
  }
}
