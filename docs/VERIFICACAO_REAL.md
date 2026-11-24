# Verificação do Caio em execução: fluxo local e limites

## Resultado desta execução

Foram executados os novos testes `tests/test_runtime_guardrails.py` junto com os testes já existentes `tests/test_interface_secure_proxy.py`: **11 passaram**. O pytest apresentou um aviso sobre `asyncio_mode` porque o carregamento automático de plugins foi desligado; esses testes são síncronos. Sem desligá-lo, nesta máquina o pytest falhou na inicialização por uma incompatibilidade entre plugins/dependências de OpenTelemetry, antes de executar qualquer teste.

Foram usados Python 3.14.4 e Node v20.19.2. Não estavam disponíveis `npm`, `npx`, Docker, Podman, `dashboard/node_modules` ou `dashboard/dist`. **Não foi feito build React nem teste da interface no navegador.** Não foram instaladas dependências, usadas contas reais, alterado deploy, feito commit ou push.

## O que roda de verdade e o que é simulado

Os novos testes sobem em processos separados o **proxy Python do projeto** e a **API FastAPI real no Uvicorn**, trocando HTTP por sockets reais em `127.0.0.1`. O proxy continua usando a porta fixa `18790` para a API, enquanto sua própria porta é atribuída pelo sistema. Ambos os sockets são reservados antes de subir os processos; se a porta da API já estiver ocupada, o teste falha em vez de conversar com outro serviço. A configuração de teste usa HOME temporário, variáveis explícitas e credenciais fictícias; processos e sockets são encerrados no fim. Arquivos e logs de teste ficam no diretório temporário do pytest. Não se faz solicitação externa; o teste não impõe um firewall de rede ao processo.

A inicialização normal do agente não é executada: a API recebe um **agente offline simulado e determinístico**, que responde sem provedor, credenciais ou ferramentas externas. O SSE usa o broadcaster e a rota reais da API, mas o evento é emitido por esse agente simulado. Há um pequeno observador ASGI apenas para conferir, por respostas booleanas, quais cabeçalhos efetivamente chegaram à API; ele não devolve o valor do token. Para o proxy iniciar, o teste cria HTML e JS de fixture temporários: **não são o build nem o código React do dashboard**. Os processos herdam sockets já vinculados, portanto o teste exercita o servidor/handler real e não pretende validar o caminho normal de bind do executável ou o Compose.

## Mapa de fluxos verificados

| Fluxo | Evidência |
|---|---|
| Status e autenticação | API direta rejeita ausência de token e token errado (401), aceita o token de teste; `/api/status` responde via proxy. O estado de agente online aqui é do agente simulado, não do Caio real. |
| Chat | POST same-origin passa pelo proxy e pela rota real; o agente simulado recebe mensagem, sessão, canal e ID e responde no formato da API. |
| Credencial | O proxy injeta o Bearer fictício correto na API, substituindo Authorization errado enviado pelo cliente; cookie, X-Forwarded-For e cabeçalho de webhook do cliente não chegam à API. Nos fluxos examinados, token e segredo de webhook não aparecem em corpos, cabeçalhos ou logs verificados. Isso não prova ausência de vazamento em todas as rotas, erros, logs ou respostas arbitrárias do backend. |
| Bloqueios | Origin estranho, Host inválido, Fetch cross-site/same-site, escrita sem Origin, travessia de caminho e acesso ao webhook via proxy são recusados. Um contador no agente verifica que as requisições de chat bloqueadas não foram processadas. |
| SSE | Um evento local sai do broadcaster real e é recebido em `text/event-stream` através do proxy enquanto o stream ainda está aberto, sem esperar o fechamento. |
| Estáticos | São servidos os arquivos fictícios e um JS inexistente retorna 404. Não comprova o funcionamento da interface. |

A suíte anterior também verifica enquadramento e cabeçalhos duplicados, limite/liberação de conexões, keepalive SSE, proteção direta de webhook e token inválido na inicialização do proxy.

## Como reproduzir

No Linux, com dependências Python já instaladas e porta local 18790 livre, na raiz do projeto:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -p no:cacheprovider \
  tests/test_runtime_guardrails.py tests/test_interface_secure_proxy.py -q
```

Não execute em paralelo com outra instância dos testes ou serviço que use a porta 18790. A nova suíte usa `pass_fds` e foi executada em Linux; não se afirma compatibilidade com Windows. Se o ambiente tiver plugins pytest funcionais e precisar de outros testes assíncronos, execute esses separadamente com os plugins apropriados.

## Próxima verificação, ainda pendente

Em ambiente seguro com as dependências já disponíveis, gerar o build React e executar a aplicação completa, primeiro sem contas externas, depois com integrações reais apenas mediante autorização. Validar no navegador carregamento, navegação, status, envio de mensagem, resposta, SSE, erros e console/rede, inclusive ausência de credenciais no bundle, respostas e logs. Testar separadamente o fluxo normal de inicialização e o Compose, suas redes e publicações de porta. Não expor o proxy à rede pública: ele é uma interface local sem autenticação individual, e outros processos locais podem acessá-lo durante o teste.
