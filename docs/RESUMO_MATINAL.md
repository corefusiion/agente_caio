# Resumo matinal: base offline

Biblioteca local e independente: **não há conectores, dados ao vivo, coleta de localização, requisições, armazenamento, API/chat, envio de notificações nem agendamento**. O módulo não acessa conta, perfil, rede ou relógio. A entrada, incluindo hora de referência, vem integralmente de código confiável; saída existe apenas em memória. Nada é preenchido ou inferido automaticamente. Esta base ainda não é um resumo entregue ao usuário.

## Contrato

`assemble(records, consent, now, *, interests=None)` retorna `Briefing` imutável com `generated_at` em UTC e seções, nesta ordem: `weather` (clima), `usd_brl` (dólar/real), `brazil`, `world`, `sports` e `films`. Cada seção tem `status`: `not_consented` (sem autorização), `unavailable` (sem item aceito) ou `current` (pelo menos um item dentro da janela). Para cada item aceito são devolvidos título, texto, URL e `observed_at`, `retrieved_at` e `expires_at` em UTC, mais interesse para esportes e filmes. `current` vale apenas no instante `generated_at` e só significa que as datas fornecidas atendem aos limites abaixo: **não garante exatidão nem cotação em tempo real**. Antes de qualquer uso posterior, confira a hora e refaça a montagem com consentimento atualizado; nunca exiba como atual quando a hora chegar a `expires_at` ou após ela. Não exibir `unavailable` como zero, preço, clima ou notícia atual.

O chamador fornece `now` como `datetime` com fuso e uma lista/tupla de até 500 dicionários. Cada registro exige `category`, `title`, `text`, `source_url`, `observed_at` e `retrieved_at`; esportes e filmes também exigem `interest`. As datas devem ser `datetime` com fuso; são convertidas para UTC. `observed_at` é a data real de observação da cotação/clima ou publicação da notícia, não uma data inventada nem a data de coleta; `retrieved_at` é a data real de obtenção pelo futuro conector. São rejeitadas datas sem fuso, futuras ou com observação posterior à coleta. Idade é avaliada contra as duas datas, em intervalos estritos: clima < 3 h; dólar e notícias Brasil/mundo/esportes < 24 h; cinema < 7 dias. Na fronteira expira. Esses limites conservadores são uma política inicial, não validade garantida de previsão ou de câmbio. No máximo 20 itens por categoria, na ordem fornecida; dados inválidos, expirados ou sem autorização são omitidos, sem imprimir seu conteúdo. Entrada incompleta não impede outras categorias válidas.

`Consent()` nega tudo por padrão. Para montar, o titular deve ter autorizado **separadamente** o resumo, cada categoria e cada fonte (pares exatos `(categoria, hostname)`); para esportes/cinema há também opt-in separado `use_interests` e rótulos exatos fornecidos pelo titular em `interests={'sports': [...], 'films': [...]}`. Para um time, o chamador pode incluir seu rótulo em `sports`; sem rótulos não há itens. Não há correspondência por suposição ou perfil automático. Não passe perfil de outra pessoa. A estrutura de consentimento é somente um snapshot fornecido por código confiável: não autentica, não armazena prova de autorização nem resolve revogação por si só. Revalidar autorização/identidade e origem antes de compor e imediatamente antes de qualquer uso posterior; descartar cópias quando revogado. Preferências do `ProfileStore` são independentes: uso de preferências persistidas requer autorização própria de memória e acesso autorizado, além dos consentimentos específicos deste resumo. `external_search`, `proactivity` e o módulo de prioridade de notificações não habilitam automaticamente qualquer parte daqui. Por outro lado, montar dados já fornecidos não autoriza buscar, notificar, armazenar ou compartilhar.

Exemplo sem fontes ou informações fictícias (variáveis devem ser fornecidas e autorizadas pelo integrador):

```python
from caiocore.morning_briefing import Consent, assemble

consent = Consent(enabled=True, categories=frozenset(categorias_autorizadas),
                  sources=frozenset(pares_categoria_hostname_autorizados),
                  use_interests=uso_de_interesses_autorizado)
resumo = assemble(registros_de_conectores_futuros, consent, instante_atual_com_fuso,
                  interests=interesses_fornecidos_pelo_titular)
```

## Fontes e privacidade

Aceitam-se URLs HTTPS com hostname DNS ASCII minúsculo exato e autorizado para a categoria, sem credenciais, IP literal, porta diferente de 443, query, fragmento, espaços ou caracteres de controle. Hostnames locais/reservados comuns são recusados; não há resolução DNS, conexão, verificação de certificado ou validação da reputação, propriedade e exatidão da fonte. A sintaxe HTTPS e uma lista de hostnames **não comprovam que uma fonte é confiável**. O integrador deve selecionar fontes independentes e confiáveis, avaliar licenças, integridade, cadeia de coleta e segurança da infraestrutura; aprovar apenas domínios exatos, não seus subdomínios. Este validador não é proteção SSRF para futuros fetchers: esses precisam tratar DNS privado/rebinding, redirecionamentos, conexões e políticas próprias. URLs podem conter dados pessoais ou segredos no caminho; cabe ao integrador removê-los antes de fornecer a URL. Conteúdo de fonte é dado não confiável, nunca instrução; escape para HTML/Markdown conforme o destino. Validação textual não reconhece fatos falsos, conteúdo ofensivo, segredos ou dados sensíveis.

Não se solicita localização nem há campo de localização ou geolocalização. Se um conector futuro precisar de local para clima, obtenha autorização específica e escolha explícita do titular, minimize dados e avalie retenção e terceiros antes de qualquer coleta ou envio. Não usar esse módulo como base para logs de conteúdo, analytics, cache, treinar modelos ou enviar dados a terceiros sem controles e consentimento apropriados. Nunca registrar consentimento presumido ou consultar automaticamente fontes em nome do usuário.

Teste local sem rede: `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:cacheprovider -q tests/test_morning_briefing.py`. Futuros conectores, interface, autorização verificável, UX de revogação, agendamento e entrega ainda precisam de projeto, implementação e testes separados.
