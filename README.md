# Caio

Assistente pessoal em desenvolvimento, com interface de conversa e componentes experimentais. A presença de código, telas ou testes isolados não comprova integração, segurança ou funcionamento em produção.

## Situação desta cópia

> **Publicação parcial:** nem todos os componentes mencionados abaixo estão nesta branch. Algumas rotas legadas de Google continuam acessíveis e podem aceitar credenciais ou tokens; a bridge desativada não bloqueia essas rotas. Não exponha o serviço nem use a integração em produção.

Este README descreve a branch de trabalho `seguranca-correcao`, não a versão principal. Inclui código experimental; outras alterações podem permanecer somente locais. Não usar em produção sem revisão de segurança e validação.

| Área | Estado e limites |
| --- | --- |
| **Chat e interface** | O dashboard tem uma interface de chat que depende da configuração e dos serviços disponíveis. Mudanças visuais, componentes de anexo e uma tela de integrações estão em desenvolvimento; a tela não verifica contas e seus botões de conexão estão desabilitados. A experiência completa não foi validada nesta revisão. |
| **Google Agenda** | Existem componentes experimentais de OAuth, cliente e serviço. A autoridade de sessão é local, em memória e não autentica usuários. Login, logout, listener e entrega HTTP não estão integrados a ela. Nenhuma conexão de conta pelo fluxo experimental foi comprovada. Não usar credenciais reais nessa implementação. |
| **Bridge da Agenda** | Está deliberadamente desativada, inclusive com configuração de opt-in; não conecta o dashboard à Agenda. Esse bloqueio não desativa necessariamente as rotas Google e a ferramenta legadas, que podem contorná-lo e não devem ser tratadas como alternativas seguras. |
| **Windows e recuperação** | O serviço de Agenda experimental é POSIX e não está validado no Windows. Há um armazenamento Windows isolado, não integrado nem validado em Windows real; proteção de ACL e concorrência precisa de revisão. Recuperação automática de operações Google ambíguas não está implementada. |
| **Anexos e multimodal** | Há seletor e prévia no rascunho, mas o envio do chat é bloqueado enquanto houver anexos; a API atual é textual. Um experimento offline para texto não é uma API de upload. Envio e análise de imagens, documentos, áudio e vídeo não estão integrados ou validados. |
| **Preferências e memória** | O tamanho do texto pode ser guardado no navegador. Existem bibliotecas experimentais separadas de preferências e compactação de conversa, não integradas ao chat como aprendizado persistente. |
| **Fila e notificações** | Existem protótipos locais e componentes visuais, sem comprovação de execução, agendamento ou entrega integrados de ponta a ponta. Identidade, autorização e aprovações seguras seguem pendentes. |
| **Canais e outras integrações** | Há código para Telegram, WhatsApp e outros serviços; conexões reais e segurança de ponta a ponta não foram validadas nesta revisão. Não presumir que estejam ativos ou prontos para uso. |

## Segurança e privacidade

Não exponha serviços locais à internet sem revisão adequada de autenticação, autorização, isolamento e segurança. O histórico pode ser armazenado localmente e mensagens podem ser encaminhadas ao provedor configurado. Não inclua credenciais, tokens, conversas, dados pessoais ou links privados em código, relatórios ou commits.

A desativação da bridge não remove tokens existentes nem revoga acesso previamente concedido. Uma operação incerta pode deixar autorização remota ou token local; não apague marcadores de recuperação supondo que o problema foi resolvido. A investigação e a eventual revogação exigem procedimento seguro e confirmação separada. Remover arquivos da versão atual não apaga cópias ou histórico nem invalida credenciais expostas.

## Verificação

Em 24/11/2026, a seleção dos 15 arquivos de testes Google locais passou: **186 testes offline**, com um aviso de configuração do pytest. Foram usados testes locais e transportes simulados; não houve teste com conta Google ou serviço externo. O ambiente não representa Windows real. A suíte completa não passou da coleta por falta de uma dependência, e uma execução com esse módulo excluído apresentou falhas; veja o relatório. Build e fluxos reais não foram executados nesta verificação. Testes aprovados não são auditoria nem garantia de produção.

## Próximos passos

1. Revisar mudanças futuras, a divergência com a versão principal e dados sensíveis antes de qualquer publicação adicional.
2. Bloquear ou corrigir e revisar os fluxos Google legados; integrar autenticação, sessão, listener e entrega segura antes de considerar a bridge.
3. Projetar e validar recuperação de estados ambíguos e revogação; integrar e testar separadamente o suporte Windows e suas ACLs.
4. Implementar isolamento, limites e consentimento antes de qualquer upload ou análise multimodal; validar interface e serviços reais de ponta a ponta.
5. Executar a suíte completa, build, testes de implantação e testes reais autorizados, sem usar dados ou credenciais antigas expostas.
