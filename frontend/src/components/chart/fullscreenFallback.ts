// Feature: professional-charting-suite
//
// Pure decision logic backing the ChartSurface fullscreen control
// (Requirements 12.4, 12.5).
//
// The actual fullscreen transition touches the DOM Fullscreen API, which is
// hard to exercise in a node test environment. The *decision* of what to
// attempt — and what to do when the attempt fails — is pure and is captured
// here so it can be unit-tested without a browser. ChartSurface calls
// `planFullscreenToggle` to choose an action and `fullscreenFailureFallback`
// to compute the resulting in-app state when a native request throws or the
// API is unavailable.

/** Snapshot of the relevant fullscreen-related state at click time. */
export interface FullscreenState {
  /** A native fullscreen element is currently active (`document.fullscreenElement`). */
  isNativeFullscreen: boolean;
  /** The in-app maximized fallback is currently active. */
  inAppFallbackActive: boolean;
  /** The surface element exposes a usable `requestFullscreen` method. */
  canRequestFullscreen: boolean;
}

/**
 * The action ChartSurface should attempt for a fullscreen-toggle click.
 *
 *   - `exit-native`        — leave the active native fullscreen;
 *   - `exit-fallback`      — leave the in-app maximized fallback;
 *   - `request-native`     — request native fullscreen on the surface;
 *   - `fallback-unavailable` — no Fullscreen API, go straight to the in-app
 *                              maximized fallback and indicate unavailability
 *                              (Requirement 12.5).
 */
export type FullscreenAction =
  | 'exit-native'
  | 'exit-fallback'
  | 'request-native'
  | 'fallback-unavailable';

/**
 * Choose the fullscreen action for the current state.
 *
 * Precedence mirrors the ChartSurface handler: exit native first, then exit an
 * in-app fallback, then request native when supported, otherwise fall back to
 * the in-app maximized view.
 */
export function planFullscreenToggle(state: FullscreenState): FullscreenAction {
  if (state.isNativeFullscreen) return 'exit-native';
  if (state.inAppFallbackActive) return 'exit-fallback';
  if (state.canRequestFullscreen) return 'request-native';
  return 'fallback-unavailable';
}

/** The in-app state to apply after a fullscreen attempt resolves. */
export interface FullscreenFallbackResult {
  /** Whether to show the "fullscreen unavailable" indication. */
  fullscreenUnavailable: boolean;
  /** Whether the in-app maximized view should be turned on. */
  shouldMaximize: boolean;
}

/**
 * Compute the state to apply when a native fullscreen request fails or is not
 * supported (Requirement 12.5): show the unavailable indication and maximize
 * in-app unless already maximized. The chart is retained and stays interactive.
 *
 * @param isAlreadyFullscreen Whether the in-app maximized flag is already set.
 */
export function fullscreenFailureFallback(
  isAlreadyFullscreen: boolean,
): FullscreenFallbackResult {
  return {
    fullscreenUnavailable: true,
    shouldMaximize: !isAlreadyFullscreen,
  };
}
