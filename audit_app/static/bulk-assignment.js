const bulkForm = document.getElementById('bulk-assignment');
if (bulkForm) {
  const boxes = Array.from(document.querySelectorAll('input[name="audit_ids"][form="bulk-assignment"]'));
  const selectAll = document.querySelector('[data-select-all]');
  const clear = bulkForm.querySelector('[data-clear-selection]');
  const submit = bulkForm.querySelector('[type="submit"]');
  const count = bulkForm.querySelector('[data-selection-count]');
  const key = `auditdesk.selected.v1.${bulkForm.dataset.owner}`;
  let selected = new Set();
  try {
    const stored = JSON.parse(sessionStorage.getItem(key));
    if (Array.isArray(stored)) selected = new Set(stored.filter((id) => /^\d+$/.test(id)).slice(0, 200));
  } catch { /* Selection still works without browser storage. */ }
  if ((new URLSearchParams(location.search).get('notice') || '').startsWith('Assignment updated for ')) selected.clear();

  function updateSelection() {
    const checked = boxes.filter((box) => box.checked).length;
    count.textContent = `${selected.size} selected${selected.size > checked ? ` · ${checked} on this page` : ''}`;
    submit.disabled = clear.disabled = selected.size === 0;
    selectAll.checked = boxes.length > 0 && checked === boxes.length;
    selectAll.indeterminate = checked > 0 && checked < boxes.length;
    bulkForm.querySelectorAll('[data-off-page-selection]').forEach((input) => input.remove());
    const displayed = new Set(boxes.map((box) => box.value));
    selected.forEach((id) => {
      if (displayed.has(id)) return;
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = 'audit_ids'; input.value = id;
      input.dataset.offPageSelection = '';
      bulkForm.append(input);
    });
    try { sessionStorage.setItem(key, JSON.stringify([...selected])); } catch { /* Optional persistence. */ }
  }
  boxes.forEach((box) => {
    box.checked = selected.has(box.value);
    box.addEventListener('change', () => {
      if (!box.checked) selected.delete(box.value);
      else if (selected.size < 200) selected.add(box.value);
      else box.checked = false;
      updateSelection();
    });
  });
  selectAll.addEventListener('change', () => {
    boxes.forEach((box) => {
      if (!selectAll.checked) selected.delete(box.value);
      else if (selected.size < 200) selected.add(box.value);
      box.checked = selected.has(box.value);
    });
    updateSelection();
  });
  clear.addEventListener('click', () => {
    selected.clear(); boxes.forEach((box) => { box.checked = false; }); updateSelection();
  });
  bulkForm.addEventListener('submit', (event) => {
    if (!selected.size) event.preventDefault();
  });
  window.addEventListener('pageshow', () => {
    boxes.forEach((box) => { box.checked = selected.has(box.value); }); updateSelection();
  });
  updateSelection();
}
