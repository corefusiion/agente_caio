# Estados de e-mail e calendário na interface

O dashboard usa `offline` como estado conservador para esses especialistas e para o serviço de e-mail. Isso significa **conexão não verificada**, não uma confirmação de desconexão. `statusDetail` distingue dados indisponíveis de uma resposta recebida sem confirmação da conexão. O indicador do serviço de e-mail atualmente só distingue online dos demais estados; ele não explica essa diferença.

Os endpoints atuais não são verificações de conexão: a caixa pode vir vazia sem canal de e-mail, e o calendário pode retornar listas vazias mesmo após falha de autenticação. O serviço de e-mail em `/api/status` indica configuração habilitada e informa tempo de resposta fixo, não uma medição real. Para informar conexão verdadeira, é preciso um sinal de saúde explícito do backend e uma interface que apresente estado desconhecido separadamente.

Em erro ou formato inválido, os arrays são mantidos vazios por compatibilidade, mas não significam caixa ou agenda comprovadamente vazias. Métricas não disponíveis são `null`; `getEmailInbox()` continua retornando um array, inclusive vazio em erro. A consulta de agentes não usa mais o cache anterior para evitar apresentar dados antigos como atuais durante falhas. Dados de respostas válidas podem ser mostrados sem afirmar que a conta está conectada.

Essas mudanças não validam visualmente a interface. Outras áreas fora deste ajuste podem conter indicadores ou exemplos fictícios; por exemplo, os serviços não relacionados ao e-mail e o log estático da página Monitoramento não foram corrigidos.
