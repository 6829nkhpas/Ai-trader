import { useChartUIStore } from '../store/useChartUIStore';

export interface DropdownItem {
  value: any;
  label: string;
  description?: string;
}

export function injectIframeDropdownStyles(doc: Document, theme: 'dark' | 'light' = 'dark') {
  let styleEl = doc.getElementById('tv-custom-dropdown-styles') as HTMLStyleElement | null;
  if (!styleEl) {
    styleEl = doc.createElement('style');
    styleEl.id = 'tv-custom-dropdown-styles';
    doc.head.appendChild(styleEl);
  }

  const isLight = theme === 'light';

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
      color: ${isLight ? '#475569' : '#94a3b8'};
      border-radius: 6px;
      cursor: pointer;
      transition: background-color 0.15s ease, color 0.15s ease;
    }
    .tv-custom-toolbar-btn:hover {
      background-color: ${isLight ? '#f1f5f9' : '#1e293b'};
      color: ${isLight ? '#0f172a' : '#f8fafc'};
    }
    .tv-custom-toolbar-btn.active {
      background-color: ${isLight ? '#e2e8f0' : '#334155'};
      color: #10b981;
    }
    .tv-custom-dropdown {
      position: absolute;
      z-index: 1000;
      min-width: 220px;
      background-color: ${isLight ? '#ffffff' : '#12141a'};
      border: 1px solid ${isLight ? '#e2e8f0' : '#2a2e39'};
      border-radius: 8px;
      box-shadow: ${isLight ? '0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.05)' : '0 10px 25px -5px rgba(0, 0, 0, 0.6)'};
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
      color: ${isLight ? '#0f172a' : '#f3f4f6'};
      text-align: left;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      transition: background-color 0.15s ease;
    }
    .tv-custom-dropdown-item:hover {
      background-color: ${isLight ? '#f1f5f9' : '#1e222d'};
    }
    .tv-custom-dropdown-item.active {
      background-color: ${isLight ? '#f8fafc' : '#1a202c'};
      font-weight: 700;
    }
    .tv-custom-dropdown-item-container {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .tv-custom-dropdown-item-desc {
      font-size: 10.5px;
      color: ${isLight ? '#64748b' : '#9ca3af'};
      font-weight: 400;
    }
    .tv-custom-dropdown-item-checkmark {
      color: #10b981;
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
  const currentTheme = useChartUIStore.getState().theme;
  injectIframeDropdownStyles(doc, currentTheme);

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

    el.innerHTML = `
      <div class="tv-custom-dropdown-item-container">
        <span>${item.label}</span>
        ${item.description ? `<span class="tv-custom-dropdown-item-desc">${item.description}</span>` : ''}
      </div>
      ${item.value === activeVal ? '<span class="tv-custom-dropdown-item-checkmark">✓</span>' : ''}
    `;

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
