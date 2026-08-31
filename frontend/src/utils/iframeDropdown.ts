// The custom toolbar buttons and dropdowns that live INSIDE the TradingView
// iframe.
//
// They cannot be styled the way the rest of the app is. TradingView renders in its
// own document, so Tailwind's classes and the `:root` / `.light` custom properties
// on the parent `<html>` do not reach them — the CSS has to be built as a string
// and injected into the iframe's own `<head>`.
//
// This used to mean a SECOND palette: two branches of hand-written hexes
// (`#252525` / `#fdfcfa`, `#94a3b8` / `#5b6675`, …) copied out of `globals.css`
// and picked by a `theme` argument. That is exactly the drift it looks like — a
// dark dropdown opening over a light chart — because the copy and the real tokens
// are two sources of truth for one palette, and because the argument could be
// passed a value that disagreed with what was actually on screen.
//
// So nothing is hardcoded now. The palette is read from the LIVE custom properties
// on the parent document at inject time, which makes the iframe UI the app's own
// colours by construction: it cannot be a theme behind, and there is no second
// palette to keep in sync when a token changes.

export interface DropdownItem {
  value: any;
  label: string;
  description?: string;
}

/**
 * One design token off the parent document, or `fallback` when it cannot be read.
 *
 * The fallbacks are the dark defaults from `globals.css`, used only when there is
 * no document at all (SSR) or the variable is genuinely undefined. They are NOT a
 * light/dark palette — the point is that only one palette exists.
 */
function token(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** The resolved palette for whatever theme the app is currently showing. */
function palette() {
  return {
    surface: token('--bg-surface', '#262626'),
    elevated: token('--bg-elevated', '#323232'),
    border: token('--border-default', '#3d3d3d'),
    textPrimary: token('--text-primary', '#f5f5f5'),
    textMuted: token('--text-muted', '#9ca3af'),
    accent: token('--color-primary', '#10b981'),
    // Read off the DOM rather than the store, because the shadow has to match
    // what is RENDERED. If the two ever disagree, the document is the one the user
    // is looking at.
    isLight:
      typeof document !== 'undefined' &&
      document.documentElement.classList.contains('light'),
  };
}

/**
 * Write (or rewrite) the injected stylesheet in `doc`.
 *
 * Safe to call repeatedly — it reuses its own `<style>` element and replaces the
 * contents, so a theme change is applied by calling it again. Takes no theme
 * argument on purpose: it reads the current one, so a caller cannot hand it a
 * stale value.
 */
export function injectIframeDropdownStyles(doc: Document) {
  let styleEl = doc.getElementById('tv-custom-dropdown-styles') as HTMLStyleElement | null;
  if (!styleEl) {
    styleEl = doc.createElement('style');
    styleEl.id = 'tv-custom-dropdown-styles';
    doc.head.appendChild(styleEl);
  }

  const c = palette();

  styleEl.textContent = `
    .tv-custom-toolbar-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      padding: 0;
      border: none;
      background: transparent;
      color: ${c.textMuted};
      border-radius: 6px;
      cursor: pointer;
      transition: background-color 0.15s ease, color 0.15s ease;
    }
    .tv-custom-toolbar-btn:hover {
      background-color: ${c.elevated};
      color: ${c.textPrimary};
    }
    .tv-custom-toolbar-btn.active {
      background-color: ${c.elevated};
      color: ${c.accent};
    }
    .tv-custom-dropdown {
      position: absolute;
      z-index: 1000;
      min-width: 220px;
      background-color: ${c.surface};
      border: 1px solid ${c.border};
      border-radius: 8px;
      box-shadow: ${
        c.isLight
          ? '0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.05)'
          : '0 10px 25px -5px rgba(0, 0, 0, 0.6)'
      };
      padding: 4px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      box-sizing: border-box;
    }
    .tv-custom-dropdown-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      padding: 8px 10px;
      border: none;
      background: transparent;
      color: ${c.textPrimary};
      text-align: left;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      transition: background-color 0.15s ease;
    }
    .tv-custom-dropdown-item:hover {
      background-color: ${c.elevated};
    }
    .tv-custom-dropdown-item.active {
      background-color: ${c.elevated};
      font-weight: 700;
    }
    .tv-custom-dropdown-item-container {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .tv-custom-dropdown-item-desc {
      font-size: 10.5px;
      color: ${c.textMuted};
      font-weight: 400;
    }
    .tv-custom-dropdown-item-checkmark {
      color: ${c.accent};
      font-weight: bold;
      font-size: 14px;
      margin-left: 8px;
    }
  `;
}

export function showIframeDropdown(
  btnEl: HTMLElement,
  items: DropdownItem[],
  activeVal: any,
  onSelect: (v: any) => void,
  doc: Document
) {
  // Rewritten on every open, so the panel matches the theme even if it changed
  // while the chart was mounted.
  injectIframeDropdownStyles(doc);

  // Close any existing dropdown first
  const existing = doc.querySelector('.tv-custom-dropdown');
  if (existing) {
    existing.remove();
    doc.querySelectorAll('.tv-custom-toolbar-btn').forEach((b) => b.classList.remove('active'));
  }

  btnEl.classList.add('active');

  const dropdown = doc.createElement('div');
  dropdown.className = 'tv-custom-dropdown';

  const rect = btnEl.getBoundingClientRect();
  dropdown.style.top = `${rect.bottom + 2}px`;
  dropdown.style.left = `${rect.left}px`;

  items.forEach((item) => {
    const el = doc.createElement('button');
    el.type = 'button';
    el.className = 'tv-custom-dropdown-item';
    if (item.value === activeVal) {
      el.classList.add('active');
    }

    // Built as nodes with `textContent` rather than assigned as an innerHTML
    // string. Every label here is a static literal today, so this is not a live
    // hole — but it is an HTML sink taking caller-supplied text, and nodes cost
    // the same to write.
    const container = doc.createElement('div');
    container.className = 'tv-custom-dropdown-item-container';
    const label = doc.createElement('span');
    label.textContent = item.label;
    container.appendChild(label);
    if (item.description) {
      const desc = doc.createElement('span');
      desc.className = 'tv-custom-dropdown-item-desc';
      desc.textContent = item.description;
      container.appendChild(desc);
    }
    el.appendChild(container);

    if (item.value === activeVal) {
      const check = doc.createElement('span');
      check.className = 'tv-custom-dropdown-item-checkmark';
      check.textContent = '✓';
      el.appendChild(check);
    }

    el.addEventListener('click', () => {
      onSelect(item.value);
      dropdown.remove();
      btnEl.classList.remove('active');
    });

    dropdown.appendChild(el);
  });

  doc.body.appendChild(dropdown);

  const outsideClick = (e: MouseEvent) => {
    if (!btnEl.contains(e.target as Node) && !dropdown.contains(e.target as Node)) {
      dropdown.remove();
      btnEl.classList.remove('active');
      doc.removeEventListener('mousedown', outsideClick);
    }
  };

  // Delay listener registration slightly to avoid closing immediately on trigger click
  setTimeout(() => {
    doc.addEventListener('mousedown', outsideClick);
  }, 50);
}
