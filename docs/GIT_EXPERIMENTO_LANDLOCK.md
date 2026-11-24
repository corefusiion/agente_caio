# Experimento local: Git permanece bloqueado

## Resultado

**Não existe Git de leitura ativo no Caio**, nem opção que consiga ativá-lo. O registry e o AgentLoop não foram alterados. `git_sandbox/contract.py` é um contrato experimental, isolado do agente: aceita apenas status, branches ou log (limite 1–50), sem caminhos, flags, refs ou argumentos extras; até uma solicitação válida com `opt_in=True` retorna `GitUnavailable`. O opt-in não é uma política de segurança ou uma funcionalidade pronta.

`git_sandbox/landlock_probe.py --probe` é um diagnóstico explicitamente invocado, executado num processo descartável e inteiramente offline com arquivos sintéticos criados pelo próprio programa. Não recebe repositório, não chama Git e não verifica a segurança de qualquer repositório ou aplicação. Seu resultado `ok` significa somente que observou os resultados esperados no experimento; **nunca habilita Git**. Não use o modo interno `--child` com dados reais.

## Evidências do ambiente testado

Em Linux x86_64, Landlock ABI 7, o experimento leu um arquivo permitido e teve bloqueados leitura fora da área, escape por symlink, abertura para escrita, criação de arquivo e execução de um binário na área. Entretanto, **um hardlink na área autorizada permitiu ler o mesmo inode de um arquivo fora dela**, e o processo conseguiu alterar as permissões de um arquivo com `chmod`. Esses são contraexemplos reais em arquivos descartáveis: Landlock sozinho não garante confidencialidade por origem nem filesystem imutável. Landlock também não restringe todos os metadados, descritores previamente abertos ou toda a rede. Não se deve tratar o teste como fronteira completa.

Nesta sessão, `unshare -Urnm` e mount de tmpfs funcionaram no teste; Docker e bwrap não estavam disponíveis. Disponibilidade do kernel não equivale a um worker seguro. O diagnóstico não inclui namespaces, bloqueio total de rede, seccomp, autenticação, um mecanismo que prove a origem dos arquivos, nem os limites de recursos de um worker. Nenhum deles está ativado pela presente alteração. O app e as demais ferramentas continuam independentes do Git.

## Testes

```
python git_sandbox/landlock_probe.py --probe
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tests/test_git_sandbox_contract.py tests/test_git_fail_closed.py tests/test_security_git_skills.py
```

Foram executados **49 testes**, incluindo contrato, entradas com paths/flags, fake Git no registry, repos temporários com config/hook/include/alternates/symlinks/hardlinks e troca concorrente de symlinks; também verifica que, mesmo com opt-in, nenhuma chamada de subprocess ocorre pelo contrato. Estes testes de repositório **não rodam operações Git** e não provam confinamento de um worker futuro. O pytest neste ambiente requer desabilitar o autoload por incompatibilidade preexistente de plugins OpenTelemetry/logfire; avisos restantes não mudam o resultado.

## Antes de uma implementação operacional

Projetar e verificar um worker efetivo, separado e descartável, com seleção administrativa explícita de somente um repositório; snapshot/montagem que garanta origem e trate hardlinks e corridas; filesystem realmente sem escrita inclusive metadados; isolamento completo de rede, FDs, processos e executáveis; configuração de Git controlada inclusive config local, includes, hooks, drivers, gitdir e alternates; sem shell, sem parâmetros arbitrários, com limits de tempo/memória/processos/saída. Validar o fluxo real em testes adversariais contra cada operação e só então considerar uma integração explícita. Windows requer solução própria.
