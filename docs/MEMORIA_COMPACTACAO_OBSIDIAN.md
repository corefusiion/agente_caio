# Proposta: compactação, memória e Obsidian

Status: arquitetura proposta, não implementada nem conectada. Não criar, sincronizar, ler ou escrever vault real sem consentimento, escopo e configuração segura. Não treina ou altera pesos de modelo.

## Diagnóstico existente

O Caio tem `MemoryStore` com memória global em Markdown e consolidação por modelo, acionada por contagem de mensagens; o histórico recente é limitado por `memory_window`. Não equivale a um orçamento confiável em tokens nem comprova isolamento ou consentimento multiusuário. Uma consolidação que recorta conteúdo pode perder detalhes. Não expor a memória atual compartilhada como se fosse isolada.

## Proposta de compactação

- Separar regras do sistema, resumo por conversa, mensagens recentes e resultado de busca autorizado; contabilizar por modelo, preferindo medição real do provedor e estimativas com margem quando indisponível. Definir limites configuráveis para contexto e resposta. Registrar custo de sumarização também: economia líquida deve ser medida, não prometida.
- Ao aproximar do limite, resumir partes antigas preservando mensagens recentes e fatos essenciais, decisões, tarefas, pendências, datas, incertezas e referências. Evitar dividir chamadas e respostas de ferramentas. A saída do resumo é conteúdo não confiável, nunca instruções de sistema.
- Guardar resumo versionado com identidade de usuário e conversa autenticadas, limites das mensagens incluídas e origem, após gravação atômica e validação. Não apagar transcrição automaticamente. Se resumo falhar ou estiver corrompido, não avançar marcador nem descartar mensagens; erro seguro e visível. Revogar acesso e exclusão também abrangem índices, resumos e backups conforme política.
- Medir entrada, saída, custo estimado e latência antes/depois em testes com dados sintéticos; compactação pode perder detalhes e há cobrança adicional pela própria sumarização.

## Memória por conversa e Obsidian

- Vault é pasta de Markdown local; integração é opcional e não exige nuvem. Escolha explícita de pasta, confirmação de escrita, privilégio mínimo e indicação de quais dados são sincronizados e com qual serviço. Obsidian não cifra o vault local automaticamente.
- Cada conversa pode ter diretório gerado com ID aleatório não identificador, resumo e notas selecionadas com referências; não gravar transcrição integral ou segredos por padrão. Pastas por conversa são organização, não autenticação. Impedir path traversal, symlinks e sobrescritas, garantir permissões de disco, controle de concorrência e isolamento por usuário real. Nenhuma integração deve depender de mero ID escolhido pelo cliente.
- Busca local indexada por usuário e permissões, passando apenas trechos pertinentes e orçamento limitado ao modelo. Referências e incertezas ficam visíveis. Markdown e resultados recuperados são dados não confiáveis, não instruções; nunca permitir que notas autorizem ações ou substituam regras.
- Interface deve permitir ver, corrigir, exportar e apagar memórias e notas, revogar conexão, definir retenção, e informar que sincronização, logs e backups podem manter cópias. Eliminação de backups requer política operacional explícita.

## Aprendizado por feedback e predição

- Opt-in separado para coleta de feedback, memória e eventual análise; desligados por padrão. Coletar correções explicitamente, registrar contexto mínimo e origem por usuário, permitir inspeção/correção/exclusão. Não deduzir fatos sensíveis, não registrar terceiros automaticamente, nem aprender com todo histórico sem limite.
- Começar com regras transparentes: sugestões de preferências como hipóteses, graduadas por evidência, nunca executar ações externas ou tornar inferências fatos sem confirmação. Aprender com correções não significa treinamento de modelo. Não instalar modelo preditivo nem enviar dados para treinamento de terceiros sem autorização específica e avaliação de privacidade.
- Comparar decisões com feedback, avaliar falsas inferências, qualidade e custo em dados sintéticos; solicitar confirmação antes de personalização com impacto. Não garantir melhora ou maior velocidade sem medição.

## Referências

- https://docs.openclaw.ai/reference/session-management-compaction/compaction
- https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching
- https://code.claude.com/docs/en/context-window
- https://obsidian.md/help/data-storage
- https://obsidian.md/help/sync/security
