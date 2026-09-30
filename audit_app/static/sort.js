document.querySelectorAll('table[data-sortable]').forEach((table) => {
  const body = table.tBodies[0];
  const headings = Array.from(table.tHead.rows[0].cells);
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

  headings.forEach((heading, column) => {
    const button = heading.querySelector('.sort-button');
    if (!button) return;

    button.addEventListener('click', () => {
      const direction = heading.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
      const kind = button.dataset.sortType;
      const grouped = table.hasAttribute('data-sort-groups');
      const rows = (grouped ? Array.from(table.tBodies).map((section) => section.rows[0]) : Array.from(body.rows))
        .map((row, index) => ({ row, index }));
      if (rows.length < 2 || rows.some(({ row }) => row.cells.length !== headings.length)) return;

      function key(row) {
        const cell = row.cells[column];
        return (cell.dataset.sort ?? cell.textContent).trim();
      }

      rows.sort((left, right) => {
        const a = key(left.row);
        const b = key(right.row);
        if (!a || a === '—') return !b || b === '—' ? left.index - right.index : 1;
        if (!b || b === '—') return -1;
        let comparison;
        if (kind === 'date') comparison = Date.parse(a) - Date.parse(b);
        else if (kind === 'number') comparison = Number(a) - Number(b);
        else comparison = collator.compare(a, b);
        return (direction === 'ascending' ? comparison : -comparison) || left.index - right.index;
      });

      headings.forEach((item) => item.removeAttribute('aria-sort'));
      heading.setAttribute('aria-sort', direction);
      if (grouped) table.append(...rows.map(({ row }) => row.parentElement));
      else body.append(...rows.map(({ row }) => row));
    });
  });
});
