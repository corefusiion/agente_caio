# Git no agente: bloqueado até existir isolamento verificável

## Estado atual

**Não há ferramenta Git liberada.** O registry continua recusando ferramentas desconhecidas ou de escrita, e o AgentLoop não registra shell nem ferramenta Git. Não é uma implementação funcional de status, diff, branch ou log. O bloqueio é intencional: não foi demonstrado um isolamento suficiente para executá-los com os requisitos pedidos. Não se deve remover o bloqueio apenas com `shell=False` ou uma lista de flags.

## Por que Git de leitura precisa de isolamento

Além dos argumentos, Git pode consultar configurações do repositório e seus includes; `core.fsmonitor`, diff drivers/textconv, hooks, pager e helpers podem executar programas. Mesmo com configurações de ambiente globais isoladas, a configuração local continua sendo relevante. Alternates de objetos, gitdir, worktrees e symlinks podem apontar para fora do workspace; repositórios parciais podem solicitar objetos a remotos. Variáveis `GIT_*` herdadas também podem alterar o comportamento. Mudanças concorrentes após validação de caminhos criam corridas. Limitar apenas a saída após `communicate()` não impede consumo de memória, nem prova ausência de escrita ou rede.

Para liberar futuramente, a implementação precisa, no mínimo:

- Fixar workspace confiável e oferecer apenas operações e parâmetros estruturados estritamente validados, sem flags, caminhos ou comandos arbitrários. Impedir add/commit/push e qualquer operação de escrita.
- Isolar **configuração do host e do repositório**, hooks, pager, external diff, textconv, helpers e variáveis herdadas; impedir carregamento de configurações indiretas e busca de objetos remotos. Aplicar política de negação de rede no sistema operacional, não apenas por convenção.
- Conter leitura e escrita por uma fronteira real do SO à prova de symlink, gitdir externo, alternates, hardlinks e corridas; escolher fail-closed quando a origem do arquivo não puder ser comprovada. Não presumir que resolver caminhos antes de chamar Git seja suficiente.
- Impor timeout, limites de memória/processos/saída enquanto lê os pipes, e encerrar todo o grupo de processos em timeout/excesso. Preservar saída confiável sem transmitir conteúdo externo.
- Ter testes adversariais efetivos contra a implementação real e concorrência, com config maliciosa, hooks, diffs, links, alternates, worktree/gitdir externo, acesso à rede, limites e injeção de parâmetros; testar também o fluxo pelo registry e pelo AgentLoop.

Uma solução possível a investigar é um processo descartável de privilégio mínimo em isolamento de filesystem e rede, com somente uma cópia sanitizada e limitada dos dados realmente necessários. A própria cópia precisa ser protegida contra corridas e origem ambígua. A disponibilidade de mecanismos do kernel, por si só, não demonstra que uma implementação esteja segura.

## Plataformas e formatos

- **Windows:** não se deve presumir disponibilidade ou equivalência de APIs Linux. Reparse points/junctions, hardlinks, NTFS e isolamento de processos/rede requerem solução e testes próprios. Sem fronteira demonstrada, permanecer bloqueado.
- **Worktrees e gitfile `.git`:** podem referenciar gitdir fora do workspace; não estão autorizados. Mesmo caminhos aparentemente internos precisam de proteção contra troca concorrente.
- **Submódulos, alternates, promisor/partial clones e configurações externas:** não presumir que sejam seguros; rejeitar enquanto não houver tratamento demonstravelmente confinado.

## Testes atuais

`tests/test_git_fail_closed.py` inicializa um repositório temporário sem commit e injeta configuração local/global adversarial, diff externo, fsmonitor, symlinks e alternate externo. Confere que Git não aparece no agente e que uma classe Git falsa não passa pela allowlist do registry. Também testa parâmetros de escape e um gitfile externo. Esses testes comprovam somente que a capacidade **está bloqueada**, não que uma ferramenta Git futura estaria segura; não executam status/diff/log em repositórios maliciosos e não validam limites de uma ferramenta inexistente.
