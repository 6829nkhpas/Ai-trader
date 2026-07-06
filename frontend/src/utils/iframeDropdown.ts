/**
 * showIframeDropdown — helper to render a custom dropdown menu inside a same-origin iframe Document
 * aligned with the trigger button element's coordinates.
 */
export interface DropdownItem {
  value: any;
  label: string;
  description?: string;
}

export function showIframeDropdown(
  btnEl: HTMLElement,
  items: DropdownItem[],
  activeVal: any,
  onSelect: (v: any) => void,
  doc: Document
) {
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
      ${item.value === activeVal ? '<span>✓</span>' : ''}
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
