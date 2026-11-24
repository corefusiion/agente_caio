# Guardas executáveis de segurança

Estes testes fixam o comportamento atual até haver isolamento confiável e uma autorização verificável por ação. Eles **não criam** isolamento nem aprovam operações. Qualquer expansão da política exige revisão explícita e testes novos; mudar o teste para fazê-lo passar não é aprovação de segurança.

## O que verificam

- `tests/test_guardrails_arquitetura.py`: política de leitura revisada, sem adições silenciosas; despacho real do `AgentLoop` e do `ToolRegistry`, com as implementações substituídas por spies inertes para impedir rede/efeitos colaterais; bloqueio de ferramentas injetadas ou que se passam por ferramentas autorizadas, mesmo com `approved` ou modo autônomo; ausência de shell, Docker e Git no registro padrão; MCP configurado não é conectado; shell direto não chama os lançadores de subprocesso testados.
- Opt-in de arquivos vem desligado por padrão; só registra leitura e listagem quando explicitamente ativado. **Mesmo ativadas, as implementações reais atualmente continuam recusando acesso a arquivos**. Sem isolamento, opt-in não é autorização para leitura.
- `tests/test_guardrails_git.py`: o contrato experimental de Git local rejeita operações válidas com e sem opt-in e não inicia um processo via `subprocess.Popen`. Ele não está ligado ao registro do agente.
- Testes existentes de segurança completam a cobertura de links, leitura de skills, restrições do contrato e confirmações genéricas com dados alterados.

## Execução local offline

Na raiz da cópia, com dependências instaladas:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider tests/test_guardrails_arquitetura.py tests/test_guardrails_git.py tests/test_security_tools.py tests/test_security_tools_approval.py tests/test_security_workspace_fail_closed.py tests/test_security_git_skills.py tests/test_git_sandbox_contract.py -k 'not kernel_probe and not probe_failure and not hostile_repository'
```

Não executar indiscriminadamente todos os testes como se fossem offline: alguns existentes criam repositórios Git descartáveis ou testam processos/isolamento. O comando acima exclui essas provas e não habilita autoload de plugins; `pytest` pode avisar que a opção `asyncio_mode` é desconhecida. Também podem aparecer avisos de dependências locais.

## Provas de regressão (mutações descartáveis)

Em uma cópia temporária de apenas código e testes, sem credenciais, seis alterações independentes produziram falhas esperadas nos guardas:

1. Ignorar `_permitted`: ferramentas impostoras passam a executar e os testes falham.
2. Adicionar `git` à tabela de leitura sem revisão: a comparação da política falha.
3. Reintroduzir `asyncio.create_subprocess_shell` no shell, mesmo retornando texto de bloqueio: o spy acusa tentativa de processo.
4. Registrar arquivos sem opt-in: o teste de configuração padrão falha.
5. Restaurar leitura de arquivo com opt-in: o teste detecta conteúdo sintético.
6. Chamar `subprocess.run(['git', 'status'])` no contrato com opt-in: `Popen` é interceptado antes de iniciar Git e as três operações com opt-in falham.

A pasta temporária foi removida. Mutações não foram feitas no código original desta cópia. Essas seis provas demonstram detecção de regressões específicas; não demonstram cobertura universal.

## Limites importantes

- O registro atualmente **anuncia** também ferramentas que são bloqueadas no despacho; estar numa definição enviada ao modelo não significa autorização para executá-la. Uma chamada direta fora do registro não está coberta pela proteção do registro. Em particular, `SandboxExecTool.execute` contém código Docker, portanto não deve ser invocado diretamente; também não se alegou que Docker seja uma fronteira segura.
- A lista revisada já contém algumas ferramentas de leitura capazes de acessar serviços reais se configuradas, como busca web e leitura de e-mail. Os testes substituem suas execuções por spies; eles não provam que a aplicação inteira é offline e não autorizam rede, WhatsApp ou produção.
- Não há mecanismo confiável de aprovação por ação no fluxo atual: confirmação genérica e `autonomous` continuam insuficientes. Os testes não implementam esse mecanismo e não provam isolamento do sistema operacional, segurança dos canais, nem cobertura de todos os caminhos alternativos.
- O teste de processos intercepta APIs específicas, não todos os mecanismos possíveis de execução; novos caminhos exigem revisão. Asserções da lista independente exigem atualização deliberada quando houver mudança autorizada de arquitetura.
