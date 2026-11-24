# Recuperação de tarefas — protótipo isolado

`caiocore.task_recovery` é uma biblioteca em memória para **simulações confiáveis offline**. Não está ligada ao backend, chat, fila, agentes, dashboard ou vídeos; não busca na web, não escolhe alternativas nem envia informações. A fila existente só registra estados e evidências, não executa tarefas nem comprova a evidência; o cancelamento da fila não interrompe um worker. O loop e os subagentes existentes não usam este controlador. Nada aqui muda esse comportamento.

**Não conectar a ferramentas reais.** O padrão `Mode.REAL` é recusado, inclusive quando declarado explicitamente; é necessário declarar `Mode.SIMULATION`. Isso é uma barreira de interface, não sandbox: Python não consegue provar que uma função injetada é simulada nem impedir que ela acesse rede, arquivos, ferramentas ou dados sensíveis. Apenas injete funções de teste confiáveis, sem efeitos reais, e um plano de fallback explícito. Nunca receba funções, planos ou classificação de idempotência de usuários não confiáveis. Não há autorização, consentimento, persistência, isolamento de processo, proteção contra código malicioso ou validação automática de segurança. Pesquisa externa, se algum dia criada, exigirá ferramenta própria, consentimento e proteção contra transmissão de dados privados; não existe aqui.

## Contrato

- `Plan((Step(...), ...), Limits(total_seconds, attempt_seconds, max_attempts), Mode.SIMULATION)` valida previamente o plano. Há 1–10 tentativas, duração total maior que zero e até 24 h, duração por tentativa maior que zero e até 1 h; a quantidade de etapas não excede `max_attempts`. Não há ciclos nem tentativas extras. Alternativas existem somente se injetadas pelo chamador, em ordem.
- `Step(operation, verify, idempotent=False)` aceita duas **funções async distintas**, sem argumentos. A operação deve retornar `AttemptOutput(ok=True, value=resultado_real)` para tentar verificação. Qualquer outro formato, `ok` diferente do booleano `True` ou exceção é falha. O verificador recebe **nenhum resultado da operação**, deve consultar fonte independente adequada e retornar literalmente `True`; rejeição, exceção, timeout ou retorno de outro tipo não são sucesso. Separar funções não prova independência nem veracidade: é dever do integrador confiável criar uma verificação genuína. Sem ela, não use.
- A tentativa inclui operação e verificação no mesmo prazo; o prazo total também se aplica. A contagem e o prazo usam relógio monotônico do event loop. Se a tentativa falhar e for marcada `idempotent=True`, passa à próxima etapa do plano se ainda houver prazo. Sem essa marcação, **nenhuma etapa seguinte executa**, mesmo após exceção, falha, timeout ou verificação inconclusiva: o efeito pode ter acontecido. A marcação é uma declaração do chamador, não uma prova; uma operação idempotente isoladamente não garante que uma alternativa diferente seja segura. Há risco de duplicação entre execuções diferentes, processos ou reinícios; não há deduplicação, transação ou rollback.
- `RecoveryRun(plan)` é de uso único. `await run.execute()` devolve `RunResult(status, attempts, value)` com `Status.SUCCEEDED`, `FAILED` ou `DEADLINE`. O valor original só é devolvido após operação e verificação bem-sucedidas; erros e resultados não verificados não aparecem como sucesso nem são retornados. `asyncio.CancelledError` é propagada, sem fallback; `run.events` registra `CANCELLED` quando o cancelamento chega durante a execução. Se a tarefa for cancelada antes de a coroutine começar, não há evento do controlador. Cancelar não desfaz efeitos.
- `run.events` é uma tupla somente de eventos ocorridos e números de tentativa: início da tentativa, retorno/falha/timeout da operação, início e resultado/falha/timeout da verificação, conclusão, falha, prazo ou cancelamento. Início significa que a etapa foi selecionada, não que tenha concluído. Não há porcentagem, progresso inventado ou callback de mensagens. O módulo não gera logs nem inclui entradas, saídas ou exceções nos eventos; `repr` do resultado e das funções também não expõe conteúdo. Saída verificada continua sensível para quem a recebe e precisa de proteção pelo chamador; funções injetadas podem gerar logs próprios. Eventos ficam somente em memória e não são uma trilha inviolável ou prova de efeito real.

## Cancelamento e limites importantes

A execução ocorre na própria tarefa asyncio, sem criar tarefa interna solta; no timeout, o controlador solicita cancelamento e aguarda a coroutine sair. Retorno tardio, inclusive quando a função captura o cancelamento do timeout, não é aceito como sucesso. Cancelamento externo é propagado mesmo se uma função cooperativa o suprimir e retornar. **Isso não garante prazo rígido**: coroutine que bloqueia o event loop ou ignora o cancelamento indefinidamente pode travar; limpeza no `finally` pode demorar. Chamadas síncronas são rejeitadas na interface, mas uma `async def` pode envolver código síncrono, threads ou criar tarefas/subprocessos próprios; o controlador não consegue interrompê-los ou recolhê-los. A tarefa externa pode até continuar efeitos depois do timeout/cancelamento. Não prometa interrupção de thread ou reversão de efeito. É preciso executor isolado e gestão de processos explícitos para garantias mais fortes. Os limites são locais ao processo, não abrangem interrupções do processo ou reinício.

## Exemplo apenas com estado simulado

```python
from caiocore.task_recovery import AttemptOutput, Limits, Mode, Plan, RecoveryRun, Step

estado = []  # simulação local, não uma ferramenta real
async def simular():
    estado.append('concluído')
    return AttemptOutput(True, 'saída efetivamente produzida')
async def conferir():
    return estado == ['concluído']  # consulta separada do resultado retornado

plano = Plan((Step(simular, conferir),), Limits(10, 5, 1), Mode.SIMULATION)
controle = RecoveryRun(plano)
resultado = await controle.execute()
# Leia resultado.status e controle.events; não imprima valor sensível em logs.
```

O exemplo ilustra o contrato e não certifica um resultado externo real.

## Testes offline

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_task_recovery.py tests/test_task_recovery_extra.py
```

Os testes usam mocks e asyncio, sem chamadas externas. Não comprovam segurança para integração com ferramentas reais, nem medem comportamentos de componentes que ignorem cancelamento indefinidamente.
