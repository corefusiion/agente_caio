import './task-queue.css';
import './local-queue-view.css';

/**
 * Visão somente leitura, opt-in e sem rede. O integrador é responsável por obter
 * os dados de fonte autorizada e verificar identidade/isolamento antes de montá-la.
 * Aceita os campos públicos da API local; não é uma integração com essa API.
 */
export type LocalQueueTask = {
  id: string;
  description: string;
  status: string;
  priority?: number;
  created_at?: string;
  updated_at?: string;
  evidence?: string | null;
  reason?: string | null;
};
export type LocalQueueViewProps =
  | { mode: 'unavailable'; reason?: string }
  | { mode: 'loading' }
  | { mode: 'error'; reason?: string }
  | { mode: 'ready'; tasks: readonly LocalQueueTask[] };

const statusLabels: Record<string, string> = {
  pendente: 'Pendente', em_andamento: 'Em andamento', concluida: 'Concluída',
  falhou: 'Falhou', bloqueada: 'Bloqueada', cancelada: 'Cancelada',
};
const isText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;

export function LocalQueueView(props: LocalQueueViewProps) {
  // Invalid responses are not silently turned into an empty or successful queue.
  const validTasks = props.mode !== 'ready' || (Array.isArray(props.tasks) && props.tasks.every(task =>
    task && typeof task === 'object' && isText(task.id) && isText(task.description) && isText(task.status)));
  return (
    <section className="task-queue local-queue-view" aria-label="Acompanhamento da fila">
      <header className="task-queue__header"><h2>Acompanhamento da fila</h2></header>
      {props.mode === 'unavailable' && <p role="status" className="task-queue__notice">Fila indisponível: {isText(props.reason) ? props.reason : 'motivo não informado.'}</p>}
      {props.mode === 'loading' && <p role="status" className="task-queue__notice">Carregando fila…</p>}
      {props.mode === 'error' && <p role="alert" className="task-queue__notice task-queue__notice--error">Não foi possível carregar a fila: {isText(props.reason) ? props.reason : 'motivo não informado.'}</p>}
      {props.mode === 'ready' && !validTasks && <p role="alert" className="task-queue__notice task-queue__notice--error">Dados da fila inválidos.</p>}
      {props.mode === 'ready' && validTasks && <>
        <p className="local-queue-view__pagination">Itens recebidos; a lista pode estar paginada.</p>
        {props.tasks.length === 0 ? <p className="task-queue__empty">Nenhuma tarefa recebida nesta página.</p> :
          <ul className="task-queue__list">{props.tasks.map(task => <li className="task-queue__item" key={task.id}>
            <div className="task-queue__item-heading"><h3>{task.description}</h3><span className="task-queue__status">{Object.prototype.hasOwnProperty.call(statusLabels, task.status) ? statusLabels[task.status] : 'Estado desconhecido'}</span></div>
            <p className="task-queue__meta">ID: {task.id}</p>
            {Number.isInteger(task.priority) && <p className="task-queue__meta">Prioridade: {task.priority}</p>}
            {isText(task.created_at) && <p className="task-queue__meta">Criada: <time>{task.created_at}</time></p>}
            {isText(task.updated_at) && <p className="task-queue__meta">Atualizada: <time>{task.updated_at}</time></p>}
            {isText(task.evidence) && <p className="task-queue__meta local-queue-view__detail">Evidência: {task.evidence}</p>}
            {isText(task.reason) && <p className="task-queue__meta local-queue-view__detail">Motivo: {task.reason}</p>}
          </li>)}</ul>}
      </>}
    </section>
  );
}
export default LocalQueueView;
