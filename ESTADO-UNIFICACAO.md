# Estado da unificação do Caio

Trabalho em andamento na cópia local, branch seguranca-correcao, baseada no commit a1762b2. A versão principal não foi alterada nem houve push ou deploy. Não usar em produção antes de revisão de segurança e integração real.

## Implementado na cópia
- Motor isolado de priorização de notificações e adaptador de consentimento do outro projeto; sem coleta, agendamento ou entrega automática integrados.
- Sugestões lexicais de skills importadas do outro projeto, isoladas e não conectadas ao chat. Direitos/licença do código anterior ainda precisam de confirmação antes de redistribuição.
- Protótipo de busca e formatação de até cinco ofertas do Mercado Livre, não registrado como ferramenta do agente. O endpoint de busca testado retornou 403; nenhuma resposta real de sucesso foi validada. Detalhes e fontes em docs/SHOPPING.md.
- Planejador isolado de mensagens de bem-estar com consentimento individual por tema, horário, silêncio e limites; sem persistência, fila, integração ou envio. Detalhes em docs/WELLBEING.md.
- Código do chat reformulado com exibição de atividade baseada em eventos existentes; o build e o funcionamento visual ainda não foram validados.
- Endurecimento parcial da API e canais e download seguro de documentos no Linux; leitura/listagem de arquivos e shell bloqueados; ferramenta de leitura de skills disponível em POSIX.

## Pendências importantes
- Um proxy local de mesma origem permite comunicação entre API e dashboard com token no servidor. Passou em testes com servidores locais e SSE. Build React, navegador, Compose e agente completo não foram validados; não expor remotamente. Detalhes em docs/INTERFACE_SEGURA.md.
- Leitura/listagem de workspace e Git permanecem bloqueados; escrita/edição diretas não devem ser usadas sem revisão de segurança. Windows sem implementação validada de isolamento.
- WhatsApp e Evolution presentes mas não testados com conta real; acesso restrito por token e lista de permissões.
- Verificar endpoint autorizado e política do Mercado Livre, preços contextuais e integrar a ferramenta ao chat. Não contornar restrições.
- Implementar consentimento persistente, coordenação transacional, agendamento e entrega dos avisos, com controles de privacidade e testes de ponta a ponta antes de ativar.
- Revisar conectores e demais componentes do assistente anterior antes de importar; não presumir que a cópia em extras está integrada ou pronta para redistribuição.
- Testar build e interface do dashboard; validar docker compose, imagens e Actions numa branch de teste antes de publicação. O último workflow público bem-sucedido se refere ao commit original, não às mudanças locais.
- Revisar todos os arquivos e possíveis dados sensíveis antes de commit, pacote ou publicação. Nenhuma garantia de segurança integral foi estabelecida.

## Verificação em 23/11/2026
Seleção de testes offline Python: 223 passaram e 1 falha esperada referente ao preflight CORS fora do proxy de mesma origem. Testes JavaScript de eventos: 4 passaram. Descoberta automática de plugins do pytest foi desativada por conflito de dependências do ambiente. O conjunto completo de testes, build do dashboard, Docker e serviços externos não foram validados.
