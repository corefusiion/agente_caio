import { Activity, Loader2, Minus } from 'lucide-react';

// Values are produced by applyTraceEvent; never pass raw SSE events here.
export type ActivityStep =
  | { kind: 'thought'; label: string; count?: number }
  | { kind: 'tool'; name: string; returned: boolean };

/** Presentational only: pending and connected must come from the actual request/stream. */
export default function ActivitySteps({ steps, pending, connected }: {
  steps: ActivityStep[];
  pending: boolean;
  connected: boolean;
}) {
  if (!pending && steps.length === 0) return null;
  const state = pending
    ? connected ? 'Aguardando resposta' : 'Aguardando resposta · etapas indisponíveis'
    : 'Atividade observada';
  return (
    <section className="trace-card" aria-label="Atividade desta conversa">
      <style>{`@keyframes caio-activity-spin { to { transform: rotate(360deg); } }
        .caio-activity-spin { animation: caio-activity-spin 1.2s linear infinite; }
        @media (prefers-reduced-motion: reduce) { .caio-activity-spin { animation: none; } }`}</style>
      <div className="trace-header">
        <Activity size={16} aria-hidden="true" /> <span>Atividade</span>
        <span className="trace-state" role="status" aria-live="polite">{state}</span>
      </div>
      {steps.length === 0 && pending && <p>Mensagem enviada. Aguardando resposta do Caio.</p>}
      {!connected && pending && <p className="trace-note">As etapas detalhadas não podem ser vinculadas com segurança a esta resposta.</p>}
      {steps.length > 0 && <ol className="trace-body" aria-label="Etapas observadas">
        {steps.map((step, index) => (
          <li className="trace-row" key={index}>
            <span className="trace-icon" aria-hidden="true">
              {step.kind === 'tool' && !step.returned && pending
                ? <Loader2 size={14} className="caio-activity-spin" /> : <Minus size={14} />}
            </span>
            {step.kind === 'thought'
              ? <span>{step.label}{step.count && step.count > 1 ? ` · ${step.count} observações${step.count === 999 ? ' ou mais' : ''}` : ''}</span>
              : <span><strong>{step.name}</strong><span className="trace-detail"> · {step.returned ? 'resultado recebido' : pending ? 'solicitada' : 'sem confirmação de resultado'}</span></span>}
          </li>
        ))}
      </ol>}
      {steps.some(step => step.kind === 'tool' && step.returned) && <p className="trace-note">Resultado recebido não indica necessariamente sucesso.</p>}
    </section>
  );
}
