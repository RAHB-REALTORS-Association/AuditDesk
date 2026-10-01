const bulkForm = document.getElementById('bulk-assignment');
if (bulkForm) {
  const boxes = Array.from(document.querySelectorAll('input[name="audit_ids"][form="bulk-assignment"]'));
  const selectAll = document.querySelector('[data-select-all]');
  const clear = bulkForm.querySelector('[data-clear-selection]');
  const submit = bulkForm.querySelector('[type="submit"]');
  const count = bulkForm.querySelector('[data-selection-count]');
  function updateSelection() {
    const selected = boxes.filter((box) => box.checked).length;
    count.textContent = `${selected} selected`;
    submit.disabled = clear.disabled = selected === 0;
    selectAll.checked = selected === boxes.length;
    selectAll.indeterminate = selected > 0 && selected < boxes.length;
  }
  boxes.forEach((box) => box.addEventListener('change', updateSelection));
  selectAll.addEventListener('change', () => {
    boxes.forEach((box) => { box.checked = selectAll.checked; });
    updateSelection();
  });
  clear.addEventListener('click', () => {
    boxes.forEach((box) => { box.checked = false; });
    updateSelection();
  });
  bulkForm.addEventListener('submit', (event) => {
    if (!boxes.some((box) => box.checked)) event.preventDefault();
  });
  window.addEventListener('pageshow', updateSelection);
  updateSelection();
}
