// Column preferences affect presentation only, never exports or stored records.
document.querySelectorAll('table[data-columns]').forEach((table) => {
  const headings = Array.from(table.tHead.rows[0].cells);
  const key = `auditdesk.columns.v1.${table.dataset.columns}`;
  let hidden = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (Array.isArray(saved)) hidden = new Set(saved.filter((value) => typeof value === 'string'));
  } catch { /* Storage can be unavailable; controls still work for this page. */ }

  const toolbar = document.createElement('div');
  toolbar.className = 'column-toolbar';
  const picker = document.createElement('details');
  picker.className = 'column-picker';
  const summary = document.createElement('summary');
  summary.textContent = 'Columns';
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
  picker.append(summary, choices, reset);
  toolbar.append(picker);
  table.closest('.table-wrap').before(toolbar);

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
  picker.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      picker.open = false;
      summary.focus();
    }
  });
});
