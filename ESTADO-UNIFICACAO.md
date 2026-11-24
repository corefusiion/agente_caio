# Estado da unificação do Caio

Atualização de 24/11/2026. Este relatório descreve código existente e limites conhecidos, não certifica segurança ou prontidão para produção. Não contém credenciais, conversas nem dados de contas.

## Branch e publicação

- Esta revisão parte da branch `seguranca-correcao`, originalmente no commit `2113b84`. As melhorias experimentais da branch e os protótipos locais não são a versão principal; parte do trabalho pode permanecer somente local.
- A `main` local está no commit `a1762b2`. A referência remota `main`, conferida nesta revisão, está em `c14b4d5`, seis commits à frente da `main` local; a branch de trabalho divergiu dessa referência. Não presumir que ela contenha as melhorias da branch. Conferir e reconciliar após revisão, sem sobrescrever o trabalho.
- Nenhum deploy foi executado nesta revisão. Mudanças privadas e protótipos não incluídos devem ser revisados separadamente, assim como o histórico e possíveis dados sensíveis, antes de publicação adicional.

## Google Agenda e bridge

- OAuth, cliente de Agenda, listener e serviço de sessão são experimentos locais. A autoridade de sessão é em memória e de um único processo; não autentica pessoas. Login, logout, listener e entrega HTTP ainda não estão integrados a essa autoridade. Não há fluxo funcional e seguro de ponta a ponta pelo dashboard, nem conta validada pelo fluxo experimental.
- A bridge do dashboard está desativada incondicionalmente, mesmo com opt-in. No proxy, após as validações gerais, o prefixo local de calendário responde indisponível ou não encontrado conforme a configuração; requisições inválidas podem ser rejeitadas antes. Uma tela de integrações mostra botões desabilitados e não comprova estado de conta.
- O bloqueio da bridge **não bloqueia necessariamente as rotas Google legadas nem a ferramenta antiga**. Elas podem contornar essa proteção e não foram tornadas seguras por este trabalho; não usá-las como alternativa. A ferramenta legada tem escopo amplo e tratamento de credenciais que requer revisão.
- O serviço experimental usa marcador e bloqueios para falhar fechado em situações de incerteza. Ainda não há recuperação automática: uma operação ambígua ou revogação não confirmada pode deixar token local ou concessão remota. Não remover o marcador presumindo que houve limpeza; a recuperação e eventual revogação exigem procedimento separado, seguro e confirmado. Desativar a bridge não apaga tokens nem revoga acesso anterior.
- O serviço é POSIX; Windows não está integrado ou validado. Existe um armazenamento isolado baseado em DPAPI, mas não estabelece nem valida ACLs e não foi testado em Windows real nesta revisão. Sua existência não significa suporte completo, segurança comprovada ou recuperação funcional.
- Referências técnicas, sujeitas a sua data e ao estado real do código: [transação de sessão](caiocore/server/GOOGLE_SESSION_TRANSACTION.md) e [bridge desativada](dashboard/GOOGLE_CALENDAR_BRIDGE.md). Documentos mais antigos podem descrever contratos superados.

## Interface, multimodal e outros componentes

- A interface inclui chat, alterações visuais e uma tela de integrações. Existe seletor e prévia de anexos, mas o envio do chat é bloqueado se houver arquivo selecionado e a rota de chat atual é apenas textual. A proposta offline de texto não implementa upload nem análise no chat. Imagem, documento, áudio e vídeo reais não estão integrados nem validados.
- Existem bases isoladas para memória, preferências, fila, priorização de notificações, avisos de bem-estar, ofertas e vídeo. Componentes isolados não demonstram aprendizado persistente no chat, execução de tarefas, agendamento, entrega, consulta externa ou análise de vídeo de ponta a ponta.
- Telegram, WhatsApp e demais integrações têm código existente, mas não foram verificados com contas reais nesta revisão. Não presumir funcionalidade, proteção completa ou prontidão para distribuição. Código importado de outros projetos requer revisão de licença e segurança.
- O proxy de mesma origem e outras medidas oferecem apenas proteção parcial; não comprovam isolamento individual completo. Não expor serviços à internet nem usar dados sensíveis antes de revisão.

## Testes e limites da verificação

- Em 24/11/2026, execução local de todos os **15 arquivos `tests/test_google*.py`**: **186 passaram**, nenhum falhou ou foi ignorado; um aviso informou que `asyncio_mode` era desconhecido, com descoberta automática de plugins desativada. Executados com Python 3.14, cache do pytest desativado e rede bloqueada em uma verificação independente.
- São testes **offline**, com simulações, incluindo handlers HTTP em memória e sockets simulados. Não verificam login real, conta ou API Google, entrega HTTP completa, sistema Windows real, falhas arbitrárias de disco ou segurança de produção. Não foram utilizadas credenciais reais nem realizadas chamadas externas nesta verificação.
- Uma tentativa da suíte completa não passou da coleta por falta do módulo `telegram`. Em outra execução offline com esse módulo excluído houve 1.054 testes aprovados, 13 falhas, 15 erros e 1 falha esperada; vários erros envolveram o bloqueio de sockets, e as demais causas exigem investigação. O build do dashboard, Docker, deploy e fluxos reais não foram executados nesta verificação. Um registro anterior de 23/11/2026 apontava 223 testes Python offline aprovados e uma falha esperada, além de quatro testes JavaScript aprovados; é resultado histórico de outra seleção, não valida o estado atual.

## Revisão posterior de dados e falhas

- Uma execução posterior da suíte sob bloqueio de rede registrou **1.092 aprovados, 14 falhas, 15 erros e 1 falha esperada**, além de 13 subtestes aprovados. São números daquela execução, anteriores às correções abaixo; a suíte completa ainda não foi repetida depois delas. Treze erros ocorreram ao reservar sockets locais bloqueados pela sandbox.
- Fixtures de testes de CLI, leitura de e-mail e proteções do agente foram ajustadas aos contratos atuais, sem ativar o shell. Em execuções offline direcionadas, passaram **186 testes Google**, **3 de CLI**, **7 de e-mail** e **3 de proteções**. A API atual não oferece o método antigo de listar mensagens não lidas; o teste adaptado verifica listagem geral e não comprova aquela funcionalidade.
- Quatro testes do onboarding exercem um fluxo interativo antigo e falham por fim da entrada; é preciso decidir se o comportamento atual preserva os requisitos antes de trocar as expectativas. Dois testes antigos de shell esperam execução hoje deliberadamente bloqueada. Um teste de vídeo excedeu seu tempo limite. A falha de agendamento requer investigação: `croniter`, declarado pelo projeto, não está disponível no Python padrão desta revisão.
- Uma busca limitada no histórico encontrou em um JSON antigo um padrão de token e um possível segredo; a validade e o serviço não foram confirmados. O arquivo não está na árvore atual da branch. A varredura foi parcial e não determina quais referências remotas contêm o objeto. Não publicar valores; identificar e, se forem credenciais, revogá-las ou rotacioná-las. Outros arquivos e o histórico completo ainda precisam de revisão, inclusive dados pessoais.

## Pendências prioritárias

1. Revisar e restringir os fluxos Google legados; integrar autenticação e posse de sessão, login/logout, listener, bridge e entrega antes de habilitar qualquer conexão real.
2. Definir e testar recuperação, transações e revogação em falhas e reinícios. Não afirmar que uma revogação ocorreu sem confirmação.
3. Projetar isolamento e permissões para Windows, integrar o armazenamento e testar em Windows real, inclusive ACLs, concorrência e falhas.
4. Implementar autenticação por usuário, isolamento de sessão, consentimento, limites de arquivos e processamento seguro antes de upload ou análise multimodal; validar a interface completa.
5. Executar testes completos e build, corrigir falhas, revisar implantação e segurança; só depois considerar testes reais autorizados, revisão final e eventual publicação.
