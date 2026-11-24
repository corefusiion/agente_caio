# Planejador isolado de bem-estar

Este módulo **só avalia e sugere texto**, localmente, quando solicitado pelo código chamador. Não agenda, envia, exibe, persiste, lê respostas, usa IA, infere dados ou chama serviços. Não está conectado ao app, WhatsApp, push, cron ou API do Caio. Nada é criado para o usuário atual ao instalar o código.

## Consentimento e privacidade

- Por padrão tudo está desligado. A pessoa precisa escolher explicitamente cada categoria (`treino`, `agua`, `mente`, `relacionamento`, `colica_ciclo`) e o aplicativo precisa também ativar a função. Nenhuma opção ou texto depende de gênero, identidade ou presunção sobre saúde, corpo ou relacionamento. Não há previsão nem rastreamento de ciclo e não se pede que a pessoa justifique sua escolha.
- A aplicação é responsável por obter e guardar o consentimento atual com segurança, por pessoa e por categoria, registrar quando foi dado (`consented_at` com fuso), e mostrar como mudar, pausar e revogar. Após revogação, usar imediatamente `consent=False` e remover a categoria se desejado; um novo opt-in exige nova data. `enabled=False` revoga/desliga tudo. `paused=True` pausa geral ou da categoria; `paused_until` pausa geral até o instante informado. Não há perguntas recorrentes de consentimento.
- `Suggestion.message` e `category` são dados privados: nunca os coloque em um push, log, URL, histórico de conversa ou serviço externo sem uma decisão de privacidade separada e consentimento aplicável. **O único texto de preview de push fornecido pelo módulo é genérico e igual para todas as categorias:** `push_title` e `push_body`; mesmo estes só podem ser mostrados por uma futura integração com consentimento e rechecagem. Não incluir categoria, nome, IDs, resposta ou tema no payload visível. Notificação em tela bloqueada pode revelar que existe uma sugestão de bem-estar; a pessoa deve poder desativá-la.
- Não captura respostas, não deduz sintomas nem faz diagnóstico; textos são sugestões sem cobrança. Este código não substitui atenção profissional nem é mecanismo de emergência.

## Uso offline

```python
from datetime import date, datetime, time, timezone
from caiocore.wellbeing import CategoryPreference, Config, suggest

# Exemplos apenas: usar os valores reais que a pessoa escolheu explicitamente.
agora = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
agua = CategoryPreference(
    category="agua", consent=True,
    consented_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    local_time=time(9, 0), every_days=1, anchor_date=date(2030, 1, 1),
)
config = Config(enabled=True, timezone="America/Bahia", categories=(agua,))
sugestao = suggest(config, "agua", agora, history=())
# sugestao é None se não for elegível; caso contrário possui message privado
# e push_title/push_body genéricos. Esta chamada não mostra nem envia nada.
```

A configuração tem um horário local por categoria por dia elegível, intervalo de 1–30 dias ancorado em data local e janela de avaliação apenas no **minuto** escolhido, sem reposição de horários perdidos. O padrão de silêncio é 22h–7h (inclusivo no início, exclusivo no fim); é configurável com fuso IANA e pode atravessar a meia-noite. Horário inexistente na troca de fuso é ignorado; horários duplicados na volta do relógio não repetem uma apresentação registrada no mesmo dia local. Limites globais móveis (últimos 60 minutos e 24 horas) são configuráveis até 2/h e 5/24h, com padrão 1/h e 3/24h. Há no máximo uma apresentação por categoria por data local. Estes limites são específicos deste planejador, não compartilhados automaticamente com outros tipos de notificação.

## Contrato obrigatório para integração futura

O chamador confiável precisa fornecer `history` completo **da mesma pessoa** como tupla de `Presentation(category, at)`, com datas e horas com fuso, registrando somente apresentações reais confirmadas, não avaliações nem tentativas falhas. Manter, no mínimo, os eventos das últimas 24 horas e do dia local atual; preservar datas futuras até resolver regressão de relógio. Se o histórico estiver incompleto ou misturado entre pessoas, os limites não são garantidos. A avaliação não registra nada, não reserva vaga e pode retornar a mesma sugestão várias vezes. O módulo não oferece proteção transacional: a futura integração deve serializar toda a sequência avaliar → revalidar consentimento/configuração → exibir de fato → registrar histórico entre todos os processos; deve abortar em falha de armazenamento ou estado desconhecido. Ao revogar ou pausar, cancelar/invalidar também itens já preparados no sistema integrador; não há fila aqui.

Categorias e horários de apresentação podem revelar dados sensíveis; se for preciso persistir configurações ou histórico, armazenar por pessoa com acesso restrito, proteção apropriada, retenção mínima e exclusão quando cabível. Não armazenar texto, respostas, sintomas ou identidade de gênero. O módulo não implementa persistência, interface de consentimento, isolamento multiusuário, revogação de itens externos ou controles de acesso: isso cabe à integração antes de qualquer uso real.

O motor existente `caiocore.notifications` foi apenas inspecionado. Não há adaptador: ele aceita outras categorias e seu inicializador cria banco e chave; mapeá-las de modo arbitrário ou iniciá-lo mudaria semântica e quebraria o isolamento. Uma futura integração deve tratar consentimento de bem-estar e do motor como independentes, manter os limites de ambos e não passar textos ou metadados sensíveis para o motor. Nunca conectar transporte sem revisão adicional de segurança e privacidade.

## Testes

Na raiz do projeto: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_wellbeing.py` (variáveis de ambiente conforme seu sistema operacional). A desativação de plugins externos evita conflito de dependências do ambiente; pode aparecer aviso sobre `asyncio_mode`, que não afeta estes testes síncronos. São offline, sem envio ou agendamento. Eles cobrem consentimento, pausas, horário/silêncio, frequência, limites, prévias genéricas, fuso/DST, entradas inválidas e regressão de relógio. Não provam segurança da interface, persistência, apresentação, concorrência ou de uma integração que ainda não existe.
