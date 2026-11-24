# Atividade do chat: eventos observados

`dashboard/src/lib/trace-events.js` processa somente `thought`, `tool_call` e `tool_result` do fluxo real `GET /api/tracing/stream` com `session_id` igual a `dashboard:${sessionId}`. Não há plano predefinido, temporizador de etapas, interpretação de texto do modelo ou confirmação de sucesso. O loop existente publica `Solicitando ferramenta: <nome>` e `Ferramenta <nome> retornou resultado.`; uma resposta de ferramenta pode conter erro. Ausência de resultado não prova falha nem sucesso.

O retorno `TraceStep[]` continua compatível com o componente `agent-plan` existente; nomes são rótulos estáticos de uma lista explícita, com fallback `Outra ferramenta`. Não expõe nome arbitrário/MCP, conteúdo, argumentos, agente, sessão ou prévia de resultado. Pensamentos consecutivos são agrupados (até 999, que significa 999 ou mais); histórico de etapas limitado a 40. Resultados sem chamada observada aparecem como resultado recebido, sem fabricar chamada. Não copie/serialize/reconstitua o array entre eventos: identificação de sessão e correlação da ferramenta permanecem em `WeakMap` privado; estados não vazios sem dono são descartados. A correlação de chamadas repetidas é melhor esforço: o SSE não fornece ID de chamada. Nenhum dado privado deve ser colocado em logs, analytics, HTML ou atributos.

## Exemplo de integração

O integrador pode substituir apenas a apresentação `AgentThinking` por `ActivitySteps`, que recebe `{steps, pending, connected}`. Importar `ActivityStep` como tipo e usar somente estados derivados de `applyTraceEvent`. O componente usa status textual, lista semântica, `role=status`, oculta ícones decorativos e respeita `prefers-reduced-motion`; não anuncia uma conclusão como sucesso. Validar o contraste e navegação por teclado na aplicação real.

Na fronteira do chat, montar por sessão para que estado antigo não apareça enquanto o efeito de troca ainda não executou:

```tsx
<AnimatedAIChat key={sessionId} sessionId={sessionId} />
```

Dentro do chat, manter um array por requisição **em memória** e limpar antes de enviar. Registrar a assinatura SSE e fechar no cleanup. Por exemplo, usando os refs e estado já existentes do chat:

```tsx
import { applyTraceEvent } from '../../lib/trace-events';
import ActivitySteps, { type ActivityStep } from './activity-steps';

// stepsRef = useRef<ActivityStep[]>([]), pendingRef = useRef(false)
// steps = useState<ActivityStep[]>([]), connected = useState(false)
useEffect(() => {
  let active = true;
  const source = new EventSource(`${BASE_URL}/api/tracing/stream`);
  source.onopen = () => { if (active) setConnected(true); };
  source.onerror = () => { if (active) setConnected(false); };
  source.onmessage = message => {
    if (!active || !pendingRef.current) return;
    try {
      const next = applyTraceEvent(stepsRef.current, JSON.parse(message.data), sessionId);
      if (next !== stepsRef.current) { stepsRef.current = next; setSteps(next); }
    } catch { /* JSON inválido é ignorado, sem registrar conteúdo */ }
  };
  return () => { active = false; pendingRef.current = false; source.close(); };
}, [sessionId]);

// Antes da chamada real: stepsRef.current = []; setSteps([]); pendingRef.current = true;
// Ao receber a resposta/erro: salvar stepsRef.current para esta resposta;
// no finally: pendingRef.current = false; limpar o estado ativo.
// Durante a chamada: <ActivitySteps steps={steps} pending={true} connected={connected} />
// Nas mensagens já recebidas: <ActivitySteps steps={message.steps} pending={false} connected={false} />
```

Nunca exibir etapas antigas em uma nova sessão: desmonte o chat com `key`, descarte/aborte retornos assíncronos antigos e limpe refs, etapas, mensagens e pending ao trocar sessão. Também descarte callbacks SSE antigos. Bloqueie chamadas simultâneas nessa sessão, inclusive em outras abas/clientes quando houver como coordená-las. O SSE **não tem ID de requisição**: mesmo seguindo isso localmente, eventos tardios ou de outra aba com a mesma sessão podem ser atribuídos à resposta errada. Para garantia real, o servidor precisa de ID de execução em cada evento e filtragem/correlação pelo ID; não inventar essa garantia no cliente.

## Limite de privacidade importante

O endpoint atual transmite os eventos brutos para todos os clientes inscritos, incluindo conteúdo, `metadata.args`, prévia de resultado e eventos de outras sessões. O filtro deste módulo protege **apenas o que é mostrado/armazenado no estado da interface**; não protege os dados no tráfego recebido por outro assinante. Antes de uso entre usuários ou sessões com isolamento de segurança, o servidor precisa autenticar/autorizar, filtrar por sessão autorizada e remover conteúdo/argumentos/resultados sensíveis antes do broadcast. Isso está fora do escopo desta alteração e não foi implementado aqui.

Teste offline: `cd dashboard && node --test tests/trace-events.test.js`.
