(() => {
  const clocks = [...document.querySelectorAll('[data-response-deadline]')].map(node => ({
    node, remaining: Date.parse(node.dataset.responseDeadline) - Date.parse(node.dataset.serverNow), start: performance.now()
  }));
  function update() {
    for (const clock of clocks) {
      const remaining = clock.remaining - (performance.now() - clock.start);
      const minutes = Math.max(1, Math.floor(Math.abs(remaining) / 60000));
      const duration = `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
      clock.node.textContent = remaining <= 0 ? `Overdue by ${duration}` : `${duration} left`;
      clock.node.className = `badge ${remaining <= 0 ? 'bad' : remaining <= 14400000 ? 'warning' : 'neutral'}`;
    }
  }
  update();
  if (clocks.length) setInterval(update, 60000);
})();
