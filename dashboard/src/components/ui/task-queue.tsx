import type { FormEvent } from 'react';
import './task-queue.css';

/** Dados e permissões devem vir de uma fonte autorizada. Este componente não faz rede,
 * não confirma operações nem altera a fila; o integrador atualiza os props após cada
 * resposta real. allowedActions não substitui autorização no servidor. */
export type TaskStatus = 'pending' | 'in_progress' | 'blocked' | 'completed' | 'failed' | 'cancelled';
export type TaskPriority = 'low' | 'normal' | 'high' | 'urgent';
export type QueueTask = {
  id: string;
  title: string;
  status: TaskStatus;
  priority?: TaskPriority;
  /** Percentual real, de 0 a 100. Ausente ou inválido significa desconhecido. */
  progress?: number | null;
  error?: string | null;
  allowedActions?: readonly ('cancel' | 'retry')[];
  /** O integrador informa se já existe uma operação pendente nesta tarefa. */
  actionPending?: boolean;
};
export type TaskQueueProps =
  | { mode: 'unavailable'; reason: string }
  | { mode: 'loading' }
  | { mode: 'error'; reason: string }
  | {
      mode: 'ready';
      tasks: readonly QueueTask[];
      /** O formulário só aparece quando o integrador fornece todos estes controles. */
      create?: {
        value: string;
        onChange: (value: string) => void;
        onCreate: (title: string) => void;
        pending?: boolean;
      };
      onCancel?: (id: string) => void;
      onRetry?: (id: string) => void;
    };

const STATUS: Record<TaskStatus, string> = {
  pending: 'Pendente', in_progress: 'Em andamento', blocked: 'Bloqueada',
  completed: 'Concluída', failed: 'Falhou', cancelled: 'Cancelada',
};
const PRIORITY: Record<TaskPriority, string> = {
  low: 'Baixa', normal: 'Normal', high: 'Alta', urgent: 'Urgente',
};

export function TaskQueue(props: TaskQueueProps) {
  return (
    <section className="task-queue" aria-label="Fila de tarefas">
      <header className="task-queue__header"><h2>Fila de tarefas</h2></header>
      {props.mode === 'unavailable' && (
        <p className="task-queue__notice" role="status">Fila indisponível: {props.reason || 'motivo não informado.'}</p>
      )}
      {props.mode === 'loading' && <p className="task-queue__notice" role="status">Carregando fila…</p>}
      {props.mode === 'error' && (
        <p className="task-queue__notice task-queue__notice--error" role="alert">Não foi possível carregar a fila: {props.reason || 'motivo não informado.'}</p>
      )}
      {props.mode === 'ready' && <ReadyQueue {...props} />}
    </section>
  );
}

type ReadyProps = Extract<TaskQueueProps, { mode: 'ready' }>;
function ReadyQueue({ tasks, create, onCancel, onRetry }: ReadyProps) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = create?.value.trim();
    if (create && !create.pending && title) create.onCreate(title);
  }
  return (
    <>
      {create && (
        <form className="task-queue__form" onSubmit={submit}>
          <label className="task-queue__field">
            <span>Nova tarefa</span>
            <input type="text" value={create.value} onChange={event => create.onChange(event.target.value)} disabled={create.pending} placeholder="Descreva a tarefa" />
          </label>
          <button type="submit" disabled={create.pending || !create.value.trim()}>{create.pending ? 'Enviando…' : 'Criar tarefa'}</button>
        </form>
      )}
      {tasks.length === 0 ? <p className="task-queue__empty">Nenhuma tarefa na fila.</p> : (
        <ul className="task-queue__list">
          {tasks.map(task => {
            const progress = task.progress;
            const hasProgress = typeof progress === 'number' && Number.isFinite(progress) && progress >= 0 && progress <= 100;
            const canCancel = !!onCancel && task.allowedActions?.includes('cancel') && ['pending', 'in_progress', 'blocked'].includes(task.status);
            const canRetry = !!onRetry && task.allowedActions?.includes('retry') && ['failed', 'cancelled'].includes(task.status);
            return (
              <li className="task-queue__item" key={task.id}>
                <div className="task-queue__item-heading"><h3>{task.title}</h3><span className="task-queue__status">{Object.prototype.hasOwnProperty.call(STATUS, task.status) ? STATUS[task.status] : 'Estado desconhecido'}</span></div>
                {task.priority && Object.prototype.hasOwnProperty.call(PRIORITY, task.priority) && <p className="task-queue__meta">Prioridade: {PRIORITY[task.priority]}</p>}
                {hasProgress && <div className="task-queue__progress"><span>Progresso: {progress}%</span><progress value={progress} max={100} aria-label={`Progresso de ${task.title}`} /></div>}
                {task.error && <p className="task-queue__error">Erro: {task.error}</p>}
                {(canCancel || canRetry) && <div className="task-queue__actions">
                  {canCancel && <button type="button" disabled={task.actionPending} aria-label={`Cancelar tarefa: ${task.title}`} onClick={() => { if (!task.actionPending) onCancel?.(task.id); }}>Cancelar</button>}
                  {canRetry && <button type="button" disabled={task.actionPending} aria-label={`Repetir tarefa: ${task.title}`} onClick={() => { if (!task.actionPending) onRetry?.(task.id); }}>Repetir</button>}
                </div>}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
export default TaskQueue;
