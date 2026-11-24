# OpenCode Console no Caio (experimental)

Integração **opt-in**, somente para o endpoint nativo OpenAI Chat Completions do OpenCode. Sem alteração da interface. Foram realizados testes simulados e testes reais limitados em 24/11/2026; disponibilidade, parâmetros, custo, créditos e suporte a tools dependem do modelo e da conta. Modelos pagos podem gerar cobrança por requisição; verifique o preço e os limites antes de ativar.

## Configuração

1. Defina `OPENCODE_API_KEY` no **ambiente do processo do Caio** por um mecanismo seguro de segredos. Não coloque a chave em arquivos JSON, no código, em histórico de comandos, logs ou Git. A chave não é copiada para o objeto de configuração. Reinicie o processo após mudar o ambiente.
2. No config existente, habilite e selecione explicitamente um modelo. Exemplo (sem chave):

```json
{
  "agents": {"defaults": {"model": "opencode/kimi-k3"}},
  "providers": {"opencode": {"enabled": true}}
}
```

Não sobrescreva as outras configurações existentes: acrescente esses campos. Por padrão está desativado. A chave em `providers.opencode.apiKey` é recusada e excluída da serialização; `extraHeaders` é recusado. `apiBase` é opcional, mas, se fornecido, deve ser exatamente `https://opencode.ai/inference/openai/v1`. O endereço efetivo da requisição é fixo: `https://opencode.ai/inference/openai/v1/chat/completions`.

O provedor é selecionado **apenas** com o prefixo `opencode/`. Não será selecionado automaticamente como fallback de outro provedor. Smart routing e fallback automático de modelos são desativados enquanto este provedor estiver em uso, para não enviar a modelos inesperados nem repetir requisições pagas. Não usa proxy de variáveis de ambiente, não segue redirects, não tenta novamente, verifica TLS e retorna erros genéricos sem detalhes do corpo ou da exceção. O conteúdo da conversa e as definições/resultados de ferramentas serão enviados à OpenCode quando o usuário ativar e usar o provedor; não envie dados que não deseje compartilhar.

## Modelos e limitações

Lista estática conservadora de modelos com endpoint nativo Chat Completions, conferida na tabela oficial: `kimi-k2.5`, `kimi-k2.6`, `kimi-k2.7-code`, `kimi-k3`, `kimi-k3.1-pro`, `glm-5.3`, `glm-5.3-flash`, `minimax-m3`, `minimax-m2.7`, `minimax-m2.5`, `deepseek-v4.1-flash`, `deepseek-v4-pro`, `qwen3.8-max`, `mistral-large-4`. Prependa `opencode/`. A lista não é atualizada automaticamente; modelos novos ou não listados são recusados até revisão. Um nome na lista não garante acesso para uma conta específica nem suporte a todas as ferramentas e parâmetros.

A API envia definições de ferramentas em formato OpenAI e interpreta `tool_calls` estruturados com argumentos JSON-objeto. A execução continua sujeita às regras do Caio. Esta implementação **não** implementa endpoints nativos Responses, Anthropic Messages ou Gemini; modelos fora da lista são recusados, mesmo quando a Console possa traduzir protocolos. A documentação de modelos menciona tradução entre APIs de texto, mas esta integração só assume e testa o endpoint nativo da tabela. Modelos gratuitos de chat são exclusivos do cliente OpenCode e podem retornar HTTP 403 em clientes externos; nenhum deles está na lista. Outros planos, endpoints Go, streaming, catálogo autenticado, imagens e cobrança detalhada não foram integrados.

## Verificação real limitada (24/11/2026)

- `deepseek-v4.1-flash` retornou uma chamada estruturada válida de `somar`; não foi testada a resposta após o resultado da ferramenta.
- `glm-5.3-flash` e `minimax-m3` retornaram a chamada estruturada e responderam corretamente após receber o resultado sintético `5`. Nenhuma ferramenta real foi executada nesses testes.
- No ciclo real do agente com `glm-5.3-flash`, uma ferramenta fictícia não permitida foi bloqueada pelo registro, e o modelo informou o bloqueio. O teste não comprova integração completa no chat ou execução de ferramentas reais.
- As sete respostas de inferência informaram, somadas, US$ 0,00054788 debitados do saldo; isso não é garantia de preços futuros. A credencial usada não foi persistida na configuração. Por ter sido compartilhada em conversa, deve ser revogada e substituída.

Fontes oficiais: [API de inferência](https://opencode.ai/docs/console/api/inference/), [modelos e endpoints nativos](https://opencode.ai/docs/console/models/), [API de listagem](https://opencode.ai/docs/console/api/models/). O catálogo pode mudar. Testes offline: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin -q tests/test_opencode_provider.py`.
