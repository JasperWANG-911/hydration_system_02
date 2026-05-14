// Render "5m ago" style timestamps from data-since ISO strings, and refresh
// the dashboard when the backend pushes an SSE event.
(() => {
  const renderAges = () => {
    document.querySelectorAll("[data-since]").forEach((el) => {
      const iso = el.dataset.since;
      if (!iso) return;
      const span = el.querySelector("span") || el;
      const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
      const text =
        mins < 1 ? "just now" :
        mins < 60 ? `${mins}m ago` :
        mins < 60 * 24 ? `${Math.floor(mins / 60)}h ago` :
        `${Math.floor(mins / 1440)}d ago`;
      span.textContent = text;
    });
  };
  renderAges();
  setInterval(renderAges, 30_000);

  if (!window.EventSource) return;
  const es = new EventSource("/sse");

  // Debounce reloads so a burst of events doesn't thrash the page.
  let reloadTimer = null;
  const scheduleReload = () => {
    if (reloadTimer) return;
    reloadTimer = setTimeout(() => location.reload(), 2000);
  };
  es.addEventListener("event", scheduleReload);
  es.addEventListener("alert", scheduleReload);
})();
