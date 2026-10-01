// Column preferences affect presentation only, never exports or stored records.
document.querySelectorAll('table[data-columns]').forEach((table) => {
  const headings = Array.from(table.tHead.rows[0].cells);
  const key = `auditdesk.columns.v1.${table.dataset.columns}`;
  let hidden = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (Array.isArray(saved)) hidden = new Set(saved.filter((value) => typeof value === 'string'));
  } catch { /* Storage can be unavailable; controls still work for this page. */ }

  const panelHead = table.closest('.panel').querySelector('.panel-head');
  const actions = document.createElement('div');
  actions.className = 'panel-head-actions';
  Array.from(panelHead.children).slice(1).forEach((child) => actions.append(child));
  const picker = document.createElement('div');
  picker.className = 'column-picker';
  picker.id = `columns-${table.dataset.columns}`;
  picker.setAttribute('popover', 'auto');
  const summary = document.createElement('button');
  summary.type = 'button';
  summary.className = 'column-toggle';
  summary.textContent = 'Filters & columns';
  summary.setAttribute('popovertarget', picker.id);
  summary.setAttribute('aria-expanded', 'false');
  summary.setAttribute('aria-controls', picker.id);
  const choices = document.createElement('div');
  choices.className = 'column-choices';
  const inputs = [];
  const identityColumn = headings.findIndex((heading) => !heading.classList.contains('audit-select'));

  headings.forEach((heading, index) => {
    if (heading.classList.contains('audit-select')) return;
    const name = heading.textContent.trim();
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.disabled = index === identityColumn || name === 'Actions';
    input.checked = input.disabled || !hidden.has(name);
    label.append(input, document.createTextNode(name));
    if (input.disabled) label.title = 'Always visible';
    choices.append(label);
    inputs.push({ input, index, name });
    input.addEventListener('change', () => {
      if (input.checked) hidden.delete(name);
      else hidden.add(name);
      apply();
      try { localStorage.setItem(key, JSON.stringify([...hidden])); } catch { /* Optional persistence. */ }
    });
  });

  const reset = document.createElement('button');
  reset.type = 'button';
  reset.textContent = 'Show all columns';
  reset.addEventListener('click', () => {
    hidden.clear();
    inputs.forEach(({ input }) => { input.checked = true; });
    apply();
    try { localStorage.removeItem(key); } catch { /* Optional persistence. */ }
  });
  const filters = table.closest('.panel').querySelector('.list-filters');
  if (filters) {
    picker.append(filters);
    if (new URLSearchParams(location.search).has('q') || new URLSearchParams(location.search).has('status')) {
      summary.classList.add('filters-active');
    }
  }
  const title = document.createElement('h3');
  title.textContent = 'Columns';
  picker.append(title, choices, reset);
  actions.append(summary);
  panelHead.append(actions);
  table.closest('.panel').append(picker);

  function positionPicker() {
    const anchor = summary.getBoundingClientRect();
    const width = Math.min(380, window.innerWidth - 32);
    picker.style.width = `${width}px`;
    picker.style.left = `${Math.max(16, Math.min(anchor.right - width, window.innerWidth - width - 16))}px`;
    picker.style.top = `${anchor.bottom + 8}px`;
    const height = picker.getBoundingClientRect().height;
    if (anchor.bottom + 8 + height > window.innerHeight - 16) {
      picker.style.top = `${Math.max(16, anchor.top - height - 8)}px`;
    }
  }
  picker.addEventListener('toggle', (event) => {
    summary.setAttribute('aria-expanded', String(event.newState === 'open'));
    if (event.newState === 'open') positionPicker();
  });
  window.addEventListener('resize', () => {
    if (picker.matches(':popover-open')) positionPicker();
  });
  window.addEventListener('scroll', () => {
    if (picker.matches(':popover-open')) positionPicker();
  }, true);

  function apply() {
    const hiddenIndexes = new Set(inputs.filter(({ input }) => !input.checked).map(({ index }) => index));
    Array.from(table.rows).forEach((row) => {
      // Empty-state rows span the whole table rather than individual columns.
      if (row.cells.length === 1 && headings.length > 1) {
        row.cells[0].colSpan = headings.length - hiddenIndexes.size;
        return;
      }
      Array.from(row.cells).forEach((cell, index) => {
        cell.classList.toggle('column-hidden', hiddenIndexes.has(index));
      });
    });
    table.classList.toggle('has-hidden-columns', hiddenIndexes.size > 0);
  }
  apply();
});
