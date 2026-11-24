# Preferências consentidas: base local, isolada

Este módulo é uma biblioteca **não conectada ao chat, app, WhatsApp, busca ou notificações**. Ele não aprende sozinho, não observa conversas, não infere preferências, não busca nada e não faz rede. A memória anterior de `caiocore/agent/memory.py` é separada, tem outra lógica de consolidação e **não é controlada nem apagada por este módulo**.

## Escopo e consentimento

Um `ProfileStore` representa um perfil em um diretório privado fornecido por quem chama. Inicialmente todos os consentimentos são falsos e não há preferências. `memory` exige opt-in explícito antes de gravar qualquer preferência. Os outros consentimentos (`questions`, `external_search`, `proactivity`) são separados e também começam desativados. Eles apenas registram uma escolha; não executam ou autorizam por si só qualquer ação futura. Cada ação futura precisará revalidar finalidade, escopo, fonte, destinatário e consentimento imediatamente antes de agir. Ativar proatividade não ativa perguntas nem busca externa; busca não ativa notificações. Não usar apostas para estimular jogos ou decisões financeiras.

Campos aceitos: `team`, `sports`, `films`, `series`, `streaming`, `betting` (opcional). Cada campo é uma lista de até 20 rótulos explícitos, de 1 a 80 caracteres imprimíveis; `None` remove um campo. Não registrar segredos, dados de saúde, dados financeiros ou outras informações sensíveis nestes rótulos. A validação estrutural não identifica automaticamente dados sensíveis: cabe ao fluxo de coleta não pedir nem guardar tais dados. Nenhum dado real vem preenchido.

## Como usar (somente código confiável)

O diretório tem que existir, ser absoluto, ser propriedade do usuário do processo e não permitir acesso a grupo/outros (0700). Seus ancestrais e o ambiente precisam estar sob controle confiável; não passar uma pasta de outro usuário. No POSIX, por exemplo, crie a pasta adequada fora do repositório com `mkdir -m 700 /caminho/privado/definido-pelo-operador`. Não envie dados de perfil para logs ou arquivos do projeto.

```python
from caiocore.personalization import ProfileStore

perfil = ProfileStore(diretorio_privado_preexistente)
estado = perfil.view()  # cópia, sem consentimento nem preferências por padrão
# Somente depois de autorização explícita do próprio titular:
perfil.set_consent("memory", True)
perfil.set_preferences({"team": lista_de_times_fornecida_pelo_titular})
perfil.set_preferences({"team": lista_corrigida})
perfil.set_preferences({"team": None})  # apaga um campo
# Separadamente e somente após autorização para esta finalidade:
perfil.set_consent("external_search", True)
perfil.set_consent("external_search", False)  # revoga só esta finalidade
perfil.set_consent("memory", False)  # remove perfil e TODOS os consentimentos
# Ou apagar tudo, inclusive se o arquivo estiver corrompido:
perfil.clear()
```

`view()` mostra consentimentos e preferências, `set_preferences` substitui os campos indicados, e `clear()` / revogação de memória removem o arquivo de perfil e os consentimentos. Um arquivo vazio de lock permanece para serializar operações. Após interrupção durante gravação, temporários órfãos do módulo são removidos na próxima operação sob lock. Apagar um arquivo não garante eliminação física em SSD, snapshots, backups, versões ou cópias já entregues a terceiros: esses sistemas precisam de política própria. A biblioteca não guarda histórico, registra data de consentimento nem fornece trilha de auditoria.

## Segurança e limites

- Requer sistema POSIX com `flock`, `O_NOFOLLOW` e permissões de proprietário; falha fechada em Windows, onde ACLs e locking ainda não foram implementados. Não se deve assumir que funciona no computador Windows do usuário.
- Pasta privada verificada em cada operação; arquivo e lock privados, regulares, de propriedade do processo e sem hard links. Links simbólicos no último componente do diretório, no perfil e no lock são recusados. O diretório é aberto por descritor para operações relativas. Usar filesystem local confiável; não foi validado em rede, containers com volumes compartilhados ou contra processo malicioso com o mesmo usuário do sistema.
- Escrita temporária 0600, `fsync`, substituição atômica, lock entre processos e threads. JSON limitado e validado; arquivos inválidos, grandes demais ou com chaves duplicadas falham fechados. Não há criptografia em repouso; outros processos com a mesma conta e administradores podem ler os dados. Não há garantia de apagar cópias em backups ou memória de outros processos.
- Não há identidade, autenticação ou autorização por usuário. Um caminho diferente por perfil evita mistura acidental, mas **não é um mecanismo de autorização**. Qualquer código que receba acesso ao diretório pode ler e modificar o perfil.

## Antes de conectar ao Caio

1. Implementar autenticação e autorização confiáveis em cada canal, mapear identidade verificada a diretório privado separado no servidor, sem usar nomes ou IDs enviados pelo cliente como caminho; isolar filesystem e verificar propriedade/ACLs. O app ainda não comprova essa identidade.
2. Criar controles reais para o titular ver/corrigir/remover cada campo, apagar tudo e ativar/revogar cada consentimento separadamente. Não tratar sugestão do modelo, mensagem de terceiro, silêncio ou escolha pré-marcada como autorização. Encaminhar apenas valores explicitamente fornecidos pelo titular; nunca consolidar chat automaticamente para este perfil.
3. Garantir que consulta externa e proatividade sejam opt-in específicos, com fontes e destinatários claros, nova checagem antes da ação e cancelamento de tarefas pendentes quando revogados. Não interpretar os flags isoladamente como capacidade de entrega, acesso a contas ou consentimento para apostas.
4. Definir retenção e remoção de backups, proteger logs e avaliar concorrência e ACLs da plataforma antes de qualquer uso. Não copiar este perfil para a memória geral, modelos externos ou ferramentas sem consentimento específico e controles de remoção correspondentes.

Teste isolado offline: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_personalization.py`. A desativação de plugins é necessária neste ambiente por um conflito de dependências do plugin instalado; também pode produzir aviso sobre configuração `asyncio_mode`.
