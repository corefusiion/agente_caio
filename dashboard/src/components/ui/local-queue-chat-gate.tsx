import './task-queue.css';

/**
 * Integração deliberadamente desconectada e sem atalho no chat.
 * Opt-in apenas exibe o diagnóstico; NÃO autoriza leitura ou envio de texto.
 *
 * A API /api/local-queue/worker, /api/local-queue/tasks e
 * /api/local-queue/text-statistics exige um token e pressupõe operador local
 * único, sem proxy. O dashboard atual não satisfaz essas premissas nem prova
 * identidade/isolamento por sessão. Não fazer fetch, usar token no navegador,
 * cachear tarefa, nem associar registros por ID de sessão fornecido pelo cliente.
 *
 * Para ativação futura: implementação separada e revisada de canal same-origin
 * direto seguro e identidade/autorização verificáveis; consentimento específico
 * por texto; teste de isolamento, reabertura, erros e resultados reais. A variável
 * deste componente nunca é capacidade de segurança nem libera essas operações.
 */
export function LocalQueueChatGate({ optIn }: { optIn?: unknown }) {
  if (optIn !== true) return null;
  return (
    <section className="task-queue" aria-label="Fila local no chat">
      <h2>Fila local indisponível no chat</h2>
      <p role="status" className="task-queue__notice">
        A conexão segura e o isolamento por sessão ainda não estão disponíveis. Nenhuma tarefa foi enviada ou consultada.
      </p>
      <p className="task-queue__notice">
        O worker existente calcula somente estatísticas locais de texto; não é IA e não executa pedidos do chat.
        Cada texto exigirá consentimento explícito antes de ser enviado.
      </p>
      <p className="task-queue__notice">
        O processamento depende do servidor ativo e não é suportado nativamente no Windows.
      </p>
    </section>
  );
}
