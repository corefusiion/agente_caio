# Busca de ofertas Mercado Livre (Brasil)

## Estado e limites

O módulo `caiocore.shopping` é isolado e somente leitura. **Não está ligado ao agente nem ao WhatsApp.** Não compra, reserva, posta, nem acessa uma conta. A busca por texto usa uma rota histórica/oficialmente exemplificada e deve ser validada com as permissões aplicáveis antes de integrar. Não foi possível verificar uma resposta de sucesso real dessa rota.

Em 23/11/2026, deste ambiente, `GET https://api.mercadolibre.com/sites/MLB/search?q=mouse&sort=price_asc&limit=5` retornou **403** (`forbidden`); `GET https://api.mercadolibre.com/sites/MLB` retornou **200**. A própria implementação retornou `access_denied`. Isso não prova que o serviço inteiro esteja fora do ar nem identifica o motivo do bloqueio. Não usar scraping, CAPTCHA, proxy para burlar restrições ou mecanismo alternativo não autorizado.

Para uso real: confirme com o Mercado Livre se a busca geral por texto está disponível para sua aplicação e se há endpoint e permissões atuais; registre a aplicação e obtenha autorização adequada quando exigida. O módulo atual **não aceita token e não implementa OAuth**; não cole segredos no código, em URL, chat, fixture ou logs. Caso seja permitida integração autenticada, faça-a em revisão posterior usando OAuth oficial, segredos no servidor, escopos mínimos, armazenamento seguro e `Authorization` apenas no cabeçalho; não presuma que token sozinho resolva o 403. Confirme também preço, condições de exibição e direitos de uso antes de publicar resultados.

## Fontes oficiais

- [Considerações de design](https://developers.mercadolivre.com.br/pt_br/busca-de-produtos-por-vendedor/consideracoes-de-design): exemplo de busca `sites/MLA/search?q=...`, paginação `limit` e `offset`; é um exemplo de documentação, não prova de disponibilidade atual para busca geral.
- [Busca de itens](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas): demonstra pesquisa por vendedor e ordenação `price_asc`, com Bearer; não confirma o contrato atual de busca geral por texto ou acesso anônimo.
- [Autenticação e autorização](https://developers.mercadolivre.com.br/pt_br/busca-de-produtos-por-vendedor/autenticacao-e-autorizacao): token no cabeçalho de autorização.
- [Erro 403](https://developers.mercadolivre.com.br/pt_br/busca-de-produtos-por-vendedor/erro-403): permissões, aplicação, usuário e outras restrições são possíveis causas.
- [Preços de produtos](https://developers.mercadolivre.com.br/pt_br/busca-de-produtos-por-vendedor/api-de-precos): documenta preço de venda contextual e descontinuação futura de campos de preço em `/items`; o preço da busca não substitui validação contextual para cada comprador.
- [Termos do programa de desenvolvedores](https://developers.mercadolivre.com.br/pt_br/termos-e-condicoes): consultar condições vigentes de acesso, direitos de conteúdo, marca e tratamento de dados. Não presumir licença irrestrita para reutilização/postagem.

## Funcionamento

`await search_offers("produto")` faz uma requisição HTTPS fixa, uma página de até 50 anúncios, solicitando `sort=price_asc`. Valida JSON, moeda BRL, preço positivo finito com centavos exatos, ID MLB e URL HTTPS de hosts oficiais específicos; descarta itens inválidos, deduplica por ID e URL e ordena localmente até cinco preços. Links são limpos de query e fragmento. O resultado é **os menores entre os anúncios recebidos**, não promessa dos cinco menores do marketplace inteiro: a rota pode filtrar, limitar ou alterar a ordenação, e preços/estoque mudam. Não promete que todos sejam promoções nem comprova desconto.

O campo `free_shipping=true` é exibido como indicação de frete grátis sujeita a confirmação no CEP. Outros casos são a calcular; não há cotação por CEP nem custo total. Ordem é somente pelo preço do produto, jamais pelo custo final. Menos de cinco resultados podem ser apresentados se houver menos elegíveis ou após deduplicação. Consulte o valor final e disponibilidade no site.

A entrada e os títulos/URLs vêm de fontes não confiáveis. O formatador escapa caracteres de Markdown no título, mas um futuro agente deve tratá-los como **dados, não instruções**, e não repassá-los como instruções de sistema. A busca é enviada ao Mercado Livre e pode passar por infraestrutura de rede/proxy configurada e aparecer nos logs de HTTP do ambiente: não pesquise dados pessoais ou segredos; configure retenção e logs de acordo com privacidade e LGPD. Nenhum dado é deliberadamente persistido pelo módulo.

Há limites de 120 caracteres na consulta, 12 s de tempo total assíncrono, timeout HTTP de 8 s por operação, máximo de 512 KiB de resposta descomprimida e 50 registros; compressão e redirecionamentos são recusados. Erros são classificados sem ecoar o corpo remoto, URL de consulta ou detalhes de rede. Não há repetição automática. Somente `httpx`, já declarado no projeto, é usado.

## Uso de desenvolvimento

```python
from caiocore.shopping import SearchError, search_offers, format_result

async def consultar(produto: str) -> str:
    try:
        return format_result(await search_offers(produto))
    except SearchError as exc:
        return str(exc)  # não inventar resultados
```

Testes sem rede (dados inteiramente fictícios):

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_shopping*.py
```

Antes de adicionar uma ferramenta ao registry, validar endpoint atual, autorização, política, preço contextual, privacidade e comportamento real fim a fim; fazer revisão de segurança e integração separada. O registry já tinha alterações alheias e foi deixado intacto.
