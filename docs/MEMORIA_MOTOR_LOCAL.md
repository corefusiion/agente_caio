# Motor experimental de compactação de conversa

## Estado e escopo

`caiocore.conversation_memory` é um motor **puro e independente**: não está ligado ao chat, Telegram, dashboard, `loop.py` ou `context.py`. Não escreve arquivo, não acessa rede, não persiste dados, não implementa Obsidian nem feedback e não substitui a memória atual. Só a biblioteca padrão é necessária. Perder o processo ou a referência ao estado perde os dados. Escolhemos esse escopo em vez de prometer escrita atômica e segura em sistemas de arquivos diferentes sem poder comprová-la.

`Identity` combina UUIDv4 de usuário e de conversa; `new_id()` gera UUIDv4. O chamador **confiável** deve gerar/guardar os IDs, autenticar o usuário e vincular cada conversa ao proprietário, sem aceitar identidade fornecida pelo cliente como prova de acesso. As operações conferem os dois IDs. Isto não é autenticação nem isolamento de segurança contra código que já tem acesso ao objeto `State` dentro do processo. Nenhum ID é caminho de arquivo; não há metadados arbitrários ou segredos no esquema. O texto, resumo e resultados de ferramenta podem conter segredos e dados pessoais.

## Funcionamento

- `State` e seus componentes são imutáveis. `append` recebe um `Turn` concluído e devolve novo estado com versão incrementada; a referência anterior não é alterada. O arquivo de turnos no estado conserva o conteúdo original durante a compactação, mas somente enquanto o estado estiver em memória.
- Um turno começa com mensagem de usuário e termina com assistente; pedidos de ferramentas (inclusive paralelos) devem ter exatamente uma resposta por ID antes da próxima mensagem de assistente. Os limites de corte são entre turnos completos; `keep_last_turns` preserva pelo menos um turno recente. Não se aceita turno em andamento, papel `system` ou outros formatos do provedor: a integração futura deve adaptar e validar sem perder informação antes de chamar o motor. Instruções de sistema e demais mensagens fora desse modelo devem ficar sob responsabilidade da integração.
- `plan` precisa de orçamento positivo e contador injetado para o contexto inteiro, incluindo resumo, papéis, chamadas/resultados e quaisquer overheads relevantes do provedor. O teste usa estimativa simples; só se pode chamar de contagem exata se o contador corresponder ao tokenizer e formato real do modelo. Se estiver dentro do orçamento, retorna `None`; se não houver turnos elegíveis, lança `BudgetExceeded`. O plano é conservador: compacta todos os turnos elegíveis, não tenta achar o menor corte possível.
- `summarize(plan, callback)` aceita callback síncrono ou assíncrono, que recebe o resumo anterior e os turnos novos em ordem. O módulo não contém nem chama serviço de rede por conta própria; um callback injetado pode fazer I/O, portanto deve ser confiável e escolhido explicitamente. O resumo anterior já pode ter perdido detalhes: o histórico completo continua disponível no estado original, mas o motor não o reapresenta automaticamente ao sumarizador em compactações seguintes.
- `apply` verifica a identidade e igualdade do estado completo com o snapshot do plano (além da versão), preserva arquivo e turnos recentes, reconta antes/depois e rejeita resumo vazio, sem economia ou acima do orçamento. `Result.saved_tokens` é diferença dos valores do contador, portanto uma estimativa quando o contador é estimado; não representa custo, qualidade ou economia real garantida. Um orçamento mal calculado ou contador instável não garante que o prompt final caiba no modelo.
- Falhas do contador/callback, resumo inválido, overflow ou plano desatualizado lançam exceção sem modificar o estado recebido. Não há truncamento automático. Um resumo muito grande ou um turno recente maior que o orçamento pode impedir a compactação; a integração precisa tratar esse caso sem apagar mensagens.

## Concorrência e limites de segurança

A checagem em `apply` **não é um commit atômico de armazenamento**. A integração precisa guardar cada conversa separadamente e executar a leitura do estado atual, `apply` e a publicação do resultado como uma única operação compare-and-swap atômica (ou sob um mesmo lock compartilhado por todas as escritas, inclusive `append`). Planejamento e chamada ao sumarizador podem ocorrer fora do lock; se houve escrita nesse intervalo, `StalePlan` exige novo plano. Locks entre threads não protegem automaticamente processos ou máquinas diferentes. Persistência futura precisará de transação/CAS durável, recuperação de falhas e política de backup próprias; este módulo não fornece isso.

Resumos podem omitir, inventar, interpretar instruções maliciosas ou resolver contradições incorretamente. Os testes verificam que informações contraditórias chegam ao callback e que os originais são preservados, **não** que um sumarizador os interprete corretamente. É preciso tratar resumo e dados de ferramentas como conteúdo não confiável e prever revisão, privacidade e retenção. Não expor estados, planos ou callbacks diretamente a clientes. Como todo o histórico fica na RAM e não há limites de tamanho implementados, uso prolongado pode esgotar memória; quotas e processamento incremental são trabalhos futuros.

## Verificação isolada

Na raiz do projeto:

```sh
python -m unittest discover -s tests -p test_conversation_memory.py -v
```

Os testes usam dados sintéticos e não fazem rede, publicações ou I/O persistente de conversas. Cobrem dois IDs de isolamento, pares de ferramentas, versões divergentes, falhas, estimador, overflow, histórico longo, concorrência com lock do chamador, callbacks síncrono/assíncrono e contradições. Isso não valida integração real, segurança de transporte, persistência, qualidade do resumo ou segurança do sistema inteiro.
