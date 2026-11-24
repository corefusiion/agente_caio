# Briefing de trechos de vídeo (base isolada)

Este pacote **não abre vídeos, extrai áudio ou quadros, transcreve, nem confirma que viu cenas**. Ele aceita somente transcrição e descrições de cena já fornecidas. Também não cria tarefas, chama chat, Telegram, IA, provedor ou rede. Ainda não está conectado à interface, à extração ou à fila de tarefas.

## Uso local, sem IA

```python
from caiocore.video_briefing import prepare, build_local

p = prepare([
    {"kind": "transcript", "start": "00:10", "end": "00:22.500",
     "text": "Precisamos estudar essa proposta."},
    {"kind": "scene_description", "start": "00:23", "end": None,
     "text": "Descrição fornecida: uma pessoa mostra um gráfico."},
])
b = build_local(p)
for s in b.summary:
    print(s.kind, s.start, s.end, s.text)
assert b.task_candidates == ()
```

O resultado é `Briefing` imutável, com `mode`, `summary`, `task_candidates` e `notice`. Cada item contém ID, tipo de fonte, timestamps originais e texto literal. **O caminho local apenas compila trechos**, não é um resumo semântico; não gera nem deduz sugestões. O chamador deve exibir esse fato, os tipos de origem e o aviso do resultado. Timestamps são fornecidos pelo chamador, não verificados contra o vídeo.

## Função de seleção injetada (opcional)

```python
from caiocore.video_briefing import run_inference

# Exemplo fictício, sem IA e sem rede:
def seletor_ficticio(payload_json):
    return {"summary": ["s1", "s2"], "task_candidates": ["s1"]}

# Somente após apresentar ao usuário o destino e os dados de p.payload(),
# e obter sua aprovação específica para esta chamada:
b = run_inference(p, seletor_ficticio, consent=True)
```

A função recebe **exatamente** a string JSON de `p.payload()`. Deve retornar um `dict` com `summary` e `task_candidates`: listas de IDs existentes, sem duplicatas, com `summary` não vazio. IDs só selecionam trechos literais, não permitem texto novo, ferramentas ou criação de tarefas. Uma seleção para tarefa é um **candidato para revisão humana**, não uma tarefa criada, nem necessariamente uma recomendação correta. A estrutura é validada, mas o significado, relevância e segurança dos trechos não podem ser garantidos. Erros da função geram `InferenceError` sem detalhes na mensagem; cancelamentos (`asyncio`, `concurrent.futures` e interrupções) propagam. Entrada ou resultado inválido lança `BriefingError`, sem briefing parcial. A função é síncrona; não há timeout nem mecanismo para cancelar uma função bloqueada. A aplicação integradora precisa prever isolamento, timeout e cancelamento adequados.

**Consentimento:** `consent=True` precisa representar aprovação explícita e atual para o conteúdo exato e o destino da função. O integrador deve apresentar previamente `p.payload()`, identificar o destino e obter a aprovação a cada chamada; o pacote verifica apenas o booleano, não autentica nem registra o consentimento. Não passe `True` automaticamente, não reutilize aprovação para outros dados ou destinatários. A função injetada é código de terceiros e pode ter efeitos, rede, registro ou reter dados: apenas o integrador pode controlar e auditar isso. Para zero transferência, use somente `build_local`.

**Dados não confiáveis:** instruções contidas nos trechos são dados, nunca ordens. O prompt orienta o seletor a tratá-las assim; não há garantia de que uma IA obedeça. Até um trecho malicioso pode ser selecionado literalmente como candidato: nunca execute texto de fonte e exija revisão antes de qualquer ação. Escape texto e metadados ao renderizar em HTML/Markdown ou outras interfaces; não interprete como instrução nem exponha indevidamente. Dados gerados não são aceitos. A função injetada não é sandbox.

## Limites

De 1 a 200 trechos; no máximo 4.000 caracteres por trecho e 200.000 bytes UTF-8 no conjunto. Cada entrada é um dicionário exato com `kind`, `start`, `end` e `text`; `kind` é `transcript` ou `scene_description`, `end` pode ser `None`. Timestamps no formato `MM:SS` ou `HH:MM:SS`, com fração opcional de 1 a 3 dígitos; minutos e segundos de 00 a 59, horas com 2 a 4 dígitos, fim não anterior ao início. Formato e texto originais são preservados. Controles de texto são recusados, exceto tabulação e quebra de linha; Unicode inválido é recusado.

Teste isolado: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_video_briefing.py`.
