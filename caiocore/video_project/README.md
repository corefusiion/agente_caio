# Contrato offline de projeto a partir de vídeo — SOMENTE MOCK

Este módulo **não é um sandbox** e não oferece criação segura de projeto real. Não há acesso a vídeo, análise de vídeo, internet, sistema de arquivos, shell, subprocessos nem execução de código. Uma instância de `VirtualTemporaryRoot` é um namespace descartável *em memória*, fornecido pelo operador: não é um diretório temporário do sistema. Nenhuma pasta, arquivo ou teste de projeto real é criado. Os únicos artefatos são strings JSON virtuais. O resumo relata estritamente ações e verificações locais realmente executadas; não declara que testes do projeto passaram.

## Contrato

1. O operador fornece briefing e plano revisado explícitos, integralmente não confiáveis, e nome curto para o projeto. Ambos os textos são necessários. Não se infere revisão de texto do vídeo. Limite: 32.768 bytes UTF-8 por texto, 16 projetos por raiz em memória; nomes ASCII com até 40 caracteres, somente letras minúsculas, números e hífen interno. Nenhum path arbitrário é aceito. A preservação do conteúdo é literal (escapes JSON não mudam o conteúdo decodificado).
2. `prepare_review(Proposal(...))` devolve o pacote com textos completos, nomes fixos dos artefatos e hash SHA-256 sobre representação canônica, versão, nome e ambos textos. Uma interface confiável deve mostrar tudo, incluindo conteúdo malicioso como dados, sem renderizá-lo como comandos/instruções nem repassá-lo a um agente que os obedeça.
3. **Só após** revisão humana específica daquele pacote, o operador digita exatamente `APROVO SIMULACAO <digest>`. `root.simulate(review, confirmation)` recalcula o pacote, rejeita alteração ou aprovação de outro conteúdo e não sobrescreve projeto virtual existente. Isso é apenas um contrato de fluxo: a frase é pública, não autentica nem prova revisão humana. Não passe aprovação gerada por IA como se fosse humana; controle de identidade/autorização e canal confiável precisam existir fora deste mock.
4. `read_virtual_artifact(nome, 'ANALISE.json' | 'RELATORIO.json')` lê apenas do namespace em memória. O relatório contém verificações de formato e round-trip realmente realizadas, com resultado individual; testes do projeto e análise do vídeo constam como não realizados.

Exemplo (mock, sem IO):
```python
from caiocore.video_project import Proposal, VirtualTemporaryRoot, prepare_review
root = VirtualTemporaryRoot()
review = prepare_review(Proposal('exemplo', 'Briefing literal', 'Plano revisado literal'))
# Exibir review completo ao operador; obter dele a confirmação vinculada a review.digest.
confirmation = 'APROVO SIMULACAO ' + review.digest  # ilustração, NÃO é aprovação humana
report = root.simulate(review, confirmation)
```

## Limites de segurança e futura implementação

Não se deve trocar o dicionário por operações de arquivos nem usar o hash ou a confirmação como autorização de segurança. Por ser tudo virtual, paths, symlinks, hardlinks e corrida de filesystem não são percorridos; isso não demonstra proteção em disco. Python compartilha o processo e a memória do chamador: monkeypatch, introspecção, falhas de runtime, ambiente hostil ou chamador não confiável estão fora da proteção. Não há isolamento de rede no SO, prevenção de importações ou limites de CPU/memória globais. Os limites de tamanho restringem a entrada deste contrato, não o processo.

Produção exigiria, antes de qualquer escrita, raiz temporária privada criada/verificada por componente confiável, isolamento efetivo e testado de SO (incluindo rede e recursos), operações relativas a descritores com proteção contra symlinks/hardlinks e races, preservação de projetos preexistentes, auditoria, limpeza segura, autenticação e autorização humana vinculada ao conteúdo exato, além de testes adversariais no ambiente alvo. Código proveniente de vídeos, internet ou LLM nunca deve ser executado neste fluxo; qualquer futuro teste de código exige aprovação separada e ambiente descartável efetivamente isolado. Se não houver garantias, permanecer no mock.
