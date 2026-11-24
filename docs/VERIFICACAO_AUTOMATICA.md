# Verificação local do Caio

Na raiz da cópia, execute:

```sh
python -B scripts/caio_verificar.py
```

É uma checagem **focada**, não uma certificação. O resultado `OK` vale apenas para as etapas abaixo e para o estado local no momento da execução. Código de saída 0 significa que todas passaram; 1 significa falha. As etapas são sequenciais e param na primeira falha, marcando as restantes como **NÃO EXECUTADO**. Arquivo ausente, coleta vazia, dependência ausente, erro, timeout, teste pulado, xfail ou xpass não viram sucesso. A saída de subprocessos e detalhes de exceções são suprimidos para evitar divulgar tokens, caminhos ou conteúdo privado. Investigue qualquer falha em ambiente seguro, sem pressupor que os detalhes de testes estejam livres de segredos.

## O que executa

1. `git diff --check` e `git diff --cached --check` verificam whitespace nas alterações rastreadas, respectivamente no diretório de trabalho e no índice. **Não verificam arquivos não rastreados** nem são revisão do conteúdo. Desabilita diff externo, textconv, fsmonitor, hooks e locks opcionais; não faz commit, altera configuração ou instala hooks.
2. Sete autotestes do verificador.
3. Guardas de arquitetura e Git: despacho, ferramentas bloqueadas, opt-in e contrato experimental.
4. Testes selecionados de segurança da API, OAuth, canais, ferramentas, workspace, bloqueio de Git e download seguro (os caminhos exatos estão em `GROUPS` no script).
5. `test_startup_fails_closed` do proxy: rejeição de token fraco. Os outros testes do proxy exigem TCP de loopback e **não são executados** por esta ferramenta.
6. Testes do cliente GitHub somente leitura com transportes simulados e testes de bem-estar e loja com dados simulados.

Os testes de fluxo local real de API e proxy são executados separadamente, pois usam TCP loopback; consulte `docs/VERIFICACAO_REAL.md`.

A suíte com risco conhecido `test_security_boundary_fixes_known_risks.py`, que contém um xfail, e os testes experimentais `test_git_sandbox_contract.py` ficam de fora. A exclusão não resolve nem elimina os riscos existentes. A lista é fixa: novos testes não entram automaticamente. O resumo informa a quantidade efetivamente aprovada por grupo.

## Pré-requisitos e rede

Requer Python 3.11+, Git, pytest e dependências necessárias aos testes já disponíveis no interpretador usado; não instala nem baixa nada. Linux x86_64 ou aarch64 com seccomp e `no_new_privs` permitidos é obrigatório; outros ambientes, como Windows direto, ou um kernel que proíba o filtro, falham fechados. O autoload de plugins pytest e o cache estão desativados; não herda tokens, proxies, variáveis Git/Python ou argumentos pytest do ambiente. É criado um HOME/TMP temporário fora do repositório, apagado ao fim. Os testes de Git criam repositórios descartáveis dentro desse temporário, não alteram o repositório do projeto.

Antes de cada etapa filha, um filtro seccomp herdado por subprocessos impede a criação de sockets não AF_UNIX (incluindo TCP/UDP IPv4 e IPv6) e bloqueia io_uring; o próprio filtro é testado antes da etapa. Portanto os testes selecionados não usam rede TCP/UDP, nem serviços externos reais. Sockets locais AF_UNIX continuam permitidos para testes internos. **Isto não é um sandbox completo para código malicioso**: AF_UNIX poderia comunicar com um intermediário local, e o filtro não isola arquivos ou todos os mecanismos do sistema. Execute somente código e dependências confiáveis, em ambiente isolado se houver suspeita de comprometimento. O proxy em produção ou serviços reais não foram exercitados.

## Fora do escopo

Não foram executados build, Docker, navegador, UI real, integração com contas ou credenciais reais, chamadas remotas, suíte integral, todos os casos do proxy ou auditoria de segurança abrangente. Nenhum desses é implícito no `OK`. Não testa integração do GitHub, bem-estar ou loja ao chat. A verificação não afirma que o Git do agente está desbloqueado ou que está seguro para escrita.

Os autotestes também podem ser rodados separadamente usando apenas a biblioteca padrão; eles simulam teste ausente e verificam a falha quando pytest falta:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B tests/test_caio_verificar.py
```
