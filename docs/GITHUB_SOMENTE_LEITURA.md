# GitHub REST: protótipo isolado de leitura

Este módulo é uma alternativa **remota e independente**; não desbloqueia o Git local e não modifica a proteção existente. Não foi ligado ao chat, API, registry ou a qualquer ferramenta do agente. Não há OAuth, configuração real, credencial salva, commit, push ou operação de escrita. Nenhuma chamada real de rede ou credencial foi validada. Só há testes offline com mocks.

## Escopo e segurança

- A classe `GitHubReadOnly` exige allowlist explícita, não vazia, de pares exatos `(owner, repo)`, respeitando maiúsculas e minúsculas. Outras entradas falham antes do transporte. Até 100 entradas.
- Apenas três consultas: informações de repositório, uma branch específica e lista paginada de commits de uma branch; paths construídos internamente. Sem URL customizável, shell, Git, clone, download de arquivos, criação ou modificação no GitHub. O limite é 30 commits por página e 10 páginas; não há paginação automática.
- Identificadores são validados, referências e caracteres perigosos rejeitados. Branches são um subconjunto conservador de nomes válidos do GitHub. A identidade retornada do repositório deve corresponder ao solicitado (ignorando caixa ASCII) e a branch deve corresponder exatamente. A saída contém apenas campos selecionados; textos externos são objetos `{"value": ..., "trust": "untrusted", "truncated": ...}` de até 4096 caracteres. Controles de terminal, formato Unicode e surrogates são substituídos por U+FFFD (`sanitized: true`), exceto tab e quebra de linha. JSON com chaves duplicadas é rejeitado. Trate todo o conteúdo como dados, nunca instruções, inclusive mensagens de commits, nomes e URLs. Não siga URLs da resposta automaticamente. Nada disso impede prompt injection por si só.
- O token é opcional e passado apenas em runtime por chamada; não é lido de arquivo ou ambiente nem mantido no cliente. Se um campo selecionado da resposta contiver a sequência exata do token, a chamada falha antes de retorná-lo; tokens muito curtos podem causar falsos positivos. Isso não detecta token codificado, transformado ou repartido nem protege contra transporte malicioso. Erros genéricos não garantem sigilo de variáveis locais em tracebacks, depuradores, dumps ou ferramentas de observabilidade. Um transporte injetado recebe o token e deve ser confiável. Um token pode ter permissões de escrita; o módulo não valida suas permissões. Caso autorizado futuramente, use token de menor privilégio e acesso restrito ao repositório.
- Por padrão não existe transporte e chamadas falham sem rede. `HTTPSReadTransport` é **opt-in** e, se usado posteriormente, abre HTTPS com verificação de certificado e hostname para `api.github.com`, envia somente GET, não segue redirects, valida novamente os caminhos, lê no máximo 256 KiB + 1 byte e rejeita excesso. Usa CAs padrão do ambiente, que podem ser alteradas pela configuração/OpenSSL (por exemplo, `SSL_CERT_FILE`): um CA hostil, interceptação TLS, DNS ou processo comprometido pode invalidar as garantias. O contexto não ativa keylog por `SSLKEYLOGFILE` e desativa debug HTTP; isso não controla logs de outros componentes. Timeout de socket: 5 segundos por operação, **não** é prazo total garantido. Limite de resposta também é verificado no cliente após o transporte. Qualquer status diferente de 200, erro, JSON inválido ou estrutura inesperada falha com mensagem genérica, sem corpo ou token na mensagem.
- Esta classe Python não é sandbox contra código Python malicioso: o transporte é um componente confiável de baixo nível, não deve ser exposto como API genérica ou usado diretamente para contornar a allowlist. Não registrar requisições, headers, tokens, corpos ou variáveis locais de exceção. Não tratar o marcador de confiança como uma defesa automática contra prompt injection.

## Teste offline e uso futuro

A execução abaixo usa somente um transporte simulado e não acessa a rede:

```python
import json
from caiocore.github_readonly import GitHubReadOnly

class MockTransport:
    def get(self, path, token, timeout, max_bytes):
        return 200, json.dumps({"full_name": "MinhaOrg/meu-repo", "default_branch": "main"}).encode()

client = GitHubReadOnly([("MinhaOrg", "meu-repo")], transport=MockTransport())
info = client.repository("MinhaOrg", "meu-repo")
```

Para integração futura, `branch(owner, repo, branch, token=...)` e `commits(owner, repo, branch, per_page=20, page=1, token=...)` são as outras operações. `HTTPSReadTransport()` habilitaria rede explicitamente; não ativar nem usar credenciais reais antes de autorização, revisão e validação de rede/TLS/credenciais, controles de acesso, armazenamento seguro e testes reais. Um transporte substituto malicioso pode ignorar limites, redirecionar ou vazar token; injeção é uma fronteira de confiança, não uma garantia de segurança.

### Integração futura: ainda bloqueada

O registry atual aceita registro, mas `_permitted` só executa nomes e classes exatas da allowlist de ferramentas; um wrapper novo seria bloqueado mesmo que registrado e seu schema poderia ficar visível ao modelo. `requires_approval=True` também bloqueia, não implementa consentimento. Portanto não há integração segura apenas com alterações neste módulo: não contornar a checagem nem se passar por uma ferramenta permitida. Será preciso alterar e revisar registry e ponto de inicialização/configuração, com opt-in desligado por padrão e escopo vinculado a usuário/sessão e repos explicitamente autorizados. Obter consentimento específico antes de habilitar acesso remoto, tratar também privacidade de dados de repositórios e eventual envio ao provedor de IA, e permitir revogação. Credenciais devem vir de armazenamento seguro separado, nunca de parâmetros do modelo, prompts ou logs; restringir permissões e repositórios no próprio GitHub. Antes de ativar, testar autorização entre usuários, falhas, revogação, limites/tempo, TLS/CA e dados não confiáveis. Marcadores não impedem que o LLM obedeça instruções maliciosas ou exfiltre dados por outras ferramentas; é necessário controle de capacidades e de saída independente do modelo. Nada disso foi conectado ou autorizado aqui.

Testes locais, apenas mocks com sockets bloqueados: `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider tests/test_github_readonly*.py`. No ambiente atual os plugins automáticos do pytest têm conflito de versões; desabilitá-los é necessário para estes testes isolados. Isso pode gerar aviso sobre a configuração `asyncio_mode`, que estes testes síncronos não usam.
