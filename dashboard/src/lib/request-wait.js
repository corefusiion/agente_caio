// This observes only the browser's pending promise and a monotonic local clock.
// It does not infer backend activity, phases, or completion percentage.
export function waitNotice(seconds) {
  if (seconds >= 60) return 'Ainda sem resposta. A conexão pode encerrar a espera em breve; isso não confirma que o trabalho parou no servidor.';
  if (seconds >= 30) return 'A resposta está demorando. Ainda não há detalhes verificáveis sobre o trabalho.';
  return 'Aguardando resposta. As etapas do trabalho não estão disponíveis.';
}

export function formatElapsed(seconds) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes} min ${rest} s` : `${rest} s`;
}

// Every tracker belongs to one request and is explicitly disposed on settlement
// or unmount. Injectable clock/timers allow deterministic lifecycle tests.
export function createWaitTracker(onUpdate, { now = () => performance.now(), schedule = setTimeout, cancel = clearTimeout } = {}) {
  const started = now();
  let stopped = false;
  let timer;
  function tick() {
    if (stopped) return;
    const seconds = Math.max(0, Math.floor((now() - started) / 1000));
    onUpdate({ seconds, notice: waitNotice(seconds) });
    if (stopped) return;
    // Update more slowly as the wait lengthens; no repeated chat messages.
    const interval = seconds < 30 ? 5000 : seconds < 60 ? 10000 : 30000;
    timer = schedule(tick, interval);
  }
  tick();
  return () => { stopped = true; if (timer !== undefined) cancel(timer); };
}
