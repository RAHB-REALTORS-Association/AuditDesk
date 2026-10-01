const copyAsana = document.querySelector('[data-copy-asana]');
if (copyAsana) {
  copyAsana.addEventListener('click', async () => {
    const details = document.getElementById('asana-details');
    const status = document.querySelector('[data-copy-status]');
    try {
      await navigator.clipboard.writeText(details.value);
      status.textContent = 'Follow-up details copied.';
    } catch {
      details.focus();
      details.select();
      status.textContent = 'Copy the selected details using your keyboard or device menu.';
    }
  });
}
