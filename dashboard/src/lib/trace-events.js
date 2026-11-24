// A stream event is evidence of an observation, not of a successful action.
export const MAX_TRACE_STEPS = 40;
const MAX_SESSION_LENGTH = 256;
const MAX_CONTENT_LENGTH = 256;
const MAX_COUNT = 999;

// Never put the server's arbitrary tool name, arguments, or result in UI state.
// Unknown/MCP names may contain personal information; only these static labels are displayed.
const TOOL_LABELS = Object.freeze({
  web_search: 'Busca na web', brave_search: 'Busca na web', tavily_search: 'Busca na web',
  web_fetch: 'Leitura de página', browse_url: 'Leitura de página',
  read_pdf: 'Leitura de documento', read_skill: 'Consulta de instruções',
  get_weather: 'Consulta de clima', get_system_status: 'Consulta de sistema',
  sandbox_exec: 'Execução de ferramenta', exec: 'Execução de ferramenta',
  generate_media: 'Ferramenta de mídia', cron: 'Ferramenta de agenda',
  message: 'Ferramenta de mensagens', workflow: 'Ferramenta de fluxo',
  desktop_control: 'Ferramenta de computador', email_delete: 'Ferramenta de e-mail',
});
const owners = new WeakMap();


/** Returns an immutable, bounded array compatible with the existing AgentThinking.
 * Only pass a fresh [] on a new request/session; see docs/ETAPAS_CHAT.md. */
export function applyTraceEvent(steps, event, sessionId, requestId) {
  if (!Array.isArray(steps)) return [];
  if (typeof sessionId !== 'string' || !sessionId.trim() || sessionId.length > MAX_SESSION_LENGTH) return steps;
  // A session alone cannot identify a request: fail closed until the server
  // provides a trustworthy request ID, verified for every event.
  if (typeof requestId !== 'string' || !requestId.trim() || requestId.length > 256) return steps;
  if (!event || typeof event !== 'object' || event.type !== 'thought' || event.session_id !== `dashboard:${sessionId}` || event.request_id !== requestId) return steps;
  if (!['thought', 'tool_call', 'tool_result'].includes(event.step)) return steps;

  // Never carry a prior session's steps forward; reject nonempty unowned state.
  const owner = owners.get(steps);
  const current = (owner?.sessionId === sessionId && owner?.requestId === requestId) || (!owner && steps.length === 0) ? steps : [];
  let next;
  if (event.step === 'thought') {
    const last = current[current.length - 1];
    if (last?.kind === 'thought') {
      if (last.count === MAX_COUNT) return current;
      next = [...current.slice(0, -1), { kind: 'thought', label: 'Processando interação', count: Math.min((last.count || 1) + 1, MAX_COUNT) }];
    } else {
      next = [...current, { kind: 'thought', label: 'Processando interação' }];
    }
  } else {
    if (typeof event.content !== 'string' || event.content.length > MAX_CONTENT_LENGTH) return current;
    const match = event.step === 'tool_call'
      ? /^Solicitando ferramenta: ([\w.-]{1,100})$/.exec(event.content)
      : /^Ferramenta ([\w.-]{1,100}) retornou resultado\.$/.exec(event.content);
    if (!match) return current;
    const key = match[1];
    const label = Object.hasOwn(TOOL_LABELS, key) ? TOOL_LABELS[key] : 'Outra ferramenta';
    // No call ID: a return cannot safely be matched to a specific invocation.
    // Present each event as a separate observation, not a completed call.
    next = [...current, { kind: 'tool', name: label, returned: event.step === 'tool_result' }];
  }
  next = next.slice(-MAX_TRACE_STEPS);
  owners.set(next, { sessionId, requestId });
  return next;
}
