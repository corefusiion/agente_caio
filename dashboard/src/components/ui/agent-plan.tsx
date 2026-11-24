import { Activity, Check, Loader2 } from 'lucide-react';

export type TraceStep = { kind: 'thought'; label: string } | { kind: 'tool'; name: string; returned: boolean };

export default function AgentThinking({ steps, pending, connected }: { steps: TraceStep[]; pending: boolean; connected: boolean }) {
  if (!pending && !steps.length) return null;
  return (
    <section className="trace-card" aria-label="Atividade desta conversa">
      <div className="trace-header">
        <Activity size={16} aria-hidden="true" />
        <span>Atividade</span>
        {pending && <span className="trace-state">{connected ? 'Aguardando resposta' : 'Aguardando resposta · eventos indisponíveis'}</span>}
      </div>
      <div className="trace-body" aria-live="polite" aria-atomic="false">
        {steps.length === 0 && pending && <p>Mensagem enviada. Aguardando resposta do Caio.</p>}
        {steps.map((step, index) => (
          <div className="trace-row" key={index}>
            <span className="trace-icon" aria-hidden="true">
              {step.kind === 'tool' && step.returned ? <Check size={14} /> : step.kind === 'tool' && pending ? <Loader2 size={14} className="trace-spin" /> : <Activity size={14} />}
            </span>
            <span>{step.kind === 'thought' ? step.label : <><strong>{step.name}</strong><span className="trace-detail"> · {step.returned ? 'resultado recebido' : pending ? 'solicitada' : 'sem confirmação de resultado'}</span></>}</span>
          </div>
        ))}
      </div>
      {steps.some(step => step.kind === 'tool' && step.returned) && <p className="trace-note">Resultado recebido não indica necessariamente sucesso.</p>}
    </section>
  );
}
