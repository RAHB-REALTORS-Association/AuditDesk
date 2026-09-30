document.querySelectorAll('.branch-toggle').forEach((button) => {
  button.addEventListener('click', () => {
    const expanded = button.getAttribute('aria-expanded') === 'true';
    const count = Number(button.dataset.count);
    button.setAttribute('aria-expanded', String(!expanded));
    button.textContent = `${expanded ? 'View' : 'Hide'} ${count} ${count === 1 ? 'branch' : 'branches'}`;
    button.closest('tbody').querySelectorAll('.branch-row').forEach((row) => {
      row.hidden = expanded;
    });
  });
});
