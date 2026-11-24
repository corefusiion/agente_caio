# Vídeo local: planejamento e extração experimental

**Estado: extração experimental comprovada apenas com dados sintéticos; desativada para uso real.** Não está conectada ao chat, à API, ao dashboard nem ao Telegram. O planejamento público permanece puro e não abre nem decodifica mídia. O novo módulo experimental contém um componente de decodificação privado, exercitado exclusivamente em um teste isolado; a função pública `extract_local` falha fechada mesmo com consentimento. Não há resumo, transcrição, inferência, tarefas nem upload. `summary`, `transcript` e `task_suggestions` do planejamento continuam `None`.

A máquina de desenvolvimento possui `ffmpeg` e `ffprobe`, mas sua presença **não** torna seguro abrir mídia não confiável. O projeto tem um experimento isolado com Landlock, que demonstra limites (inclusive aliases por hardlink); não é um sandbox pronto para este uso. Embora um teste de namespace de rede/montagem tenha funcionado neste ambiente, não se verificou uma contenção integral de processos, arquivos, credenciais e rede para um decoder vulnerável, nem garantias equivalentes no Windows. Por isso a interface pública falha fechada: **não há executor seguro de produção nem opção de habilitá-lo**, inclusive em Windows. Uma lista de protocolos do FFmpeg, sozinha, não é sandbox.

## Contrato atual

```python
from caiocore.video_analysis import MediaMetadata, make_plan

# Valores apenas declarados pelo chamador; não são dados verificados do arquivo.
m = MediaMetadata(file_bytes=100000, duration_seconds=12.0,
                  frame_count=120, width=640, height=360, has_audio=True)
p = make_plan(m, consent=True)
assert not p.metadata_verified and not p.extraction_performed
assert not p.inference_performed and p.summary is None and p.transcript is None
# p.frame_times_seconds e p.audio_windows são somente posições planejadas.
```

Somente `consent=True` explícito autoriza criar o plano inerte, não a extração, uso de modelo, divulgação de dados ou execução de tarefas. O pacote não aceita caminho, URL ou descritor de arquivo. Campos são declarações não confiáveis, e limites não verificam o arquivo real: 256 MiB, 0,001–7200 segundos, 360.000 quadros, dimensão máxima de 4096 e até 4096×2160 pixels. Propõe até cinco tempos de quadros e, se informado áudio, três trechos de até três segundos. Rejeita ausência de metadados obrigatórios, valores não finitos ou limites excedidos. Limites propostos para implementação futura: 1 MiB por imagem, 96.000 bytes por trecho PCM e 60 segundos por worker; **não são controles efetivos agora**. A geração de plano por si só não realiza alocação de mídia.

## Testes e privacidade

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_video_analysis_planning.py` executa testes de validação/consentimento e mocks contra acesso a arquivos, processos e sockets na função pura. Um teste opcional usa o FFmpeg para **gerar do zero** um pequeno vídeo azul sintético conhecido, e ffprobe para ler seus metadados; ele não usa arquivos do usuário e não passa por uma interface de extração do pacote. Os comandos são fixos, sem shell, com ambiente mínimo e timeout; o `ffprobe` limita os protocolos. O fixture temporário fica em diretório privado do sistema (`TemporaryDirectory`) e é removido após o teste. Essa prova **não** demonstra proteção adequada para mídia hostil, não valida execução em Windows e não habilita o processamento de arquivos reais. Se não houver ferramentas locais, o teste sintético é pulado.

## Experimento de extração por bytes

`caiocore.video_analysis.experimental_extract` contém `extract_local`, sempre bloqueada, e `_extract_in_preisolated_worker`, **privada, exclusiva para testes sintéticos**, que não cria nem verifica o sandbox. Não chamar a função privada no processo do Caio, nem passar vídeo de usuário, URL ou caminho. Ela não é fronteira de segurança; não existe chave de configuração para ativá-la. O consentimento explícito para a prova não autoriza análise, publicação ou uso de modelo.

O worker experimental recebe apenas `bytes` (máximo 2 MiB); exige assinatura NUT, exatamente uma faixa MPEG-4 e uma PCM s16le, ambos nesta ordem, e dimensões de até 640×360. Força demuxer NUT, lista de codecs e protocolo `pipe`; `ffprobe` e `ffmpeg` são iniciados por caminhos fixos, sem shell e com descritores fechados. As saídas são um quadro RGB24 com tamanho validado e até 1 segundo de áudio mono PCM 16 kHz (32.000 bytes); não há arquivo temporário, embedding ou envio externo na nova implementação. A leitura não bloqueante impõe tetos de saída, timeout de 8 s por processo, CPU de 5 s por processo e limite de memória de 1 GiB, além de limitar arquivos/core e descritores; processos são encerrados ao falhar. Não se estabelece o limite da **duração total do vídeo**: NUT transmitido pelo pipe do teste não fornece duração confiável. Limitar a amostra a 1 s não valida a duração integral. Tampouco se estabelece limite seguro do número total de processos; limites de CPU são por processo, e grupo de processos não substitui cgroup/isolamento. Não processar mídia real enquanto essas lacunas existirem.

Teste: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_video_experimental_extract.py`. Ele gera um NUT azul com tom de 440 Hz exclusivamente por pipe, rejeita mídia sintética truncada e verifica bytes reais do quadro e do áudio. No Linux x86_64 de desenvolvimento, usa namespaces de usuário/rede e regras Landlock com permissões somente para executáveis, loader, bibliotecas, cache e `/dev/null`; checa que `/etc/passwd` não é legível. O teste é pulado quando faltam ferramentas/namespace/ABI necessário, não fica habilitado. As bibliotecas permitidas ainda são legíveis (inclusive possíveis aliases de hardlink), sockets locais e privilégios/recursos de um decoder comprometido não foram integralmente contidos. Isso **não prova segurança contra mídia hostil** nem equivale a um sandbox de produção, nem valida Windows. Os testes antigos de planejamento ainda incluem uma prova sintética separada que cria e remove um arquivo temporário, conforme seção anterior.

## Condições antes de processar vídeo real

- Projetar e validar isolamento efetivo de processos contra rede e leitura de secrets, inclusive protocolos indiretos, bibliotecas e parser comprometido; ambiente mínimo e sem herança de descritores. Não confiar só em whitelist, nomes de arquivo ou na ausência de URL.
- Abertura explicitamente autorizada com anti-symlink de todos os componentes, validação de arquivo regular, propriedade e proteção contra troca/alteração durante leitura; fechar em caso de impossibilidade ou ambiguidade, inclusive Windows. Avaliar hardlinks, TOCTOU e semântica da plataforma.
- Extrair metadados reais antes de aceitar o planejamento; recusar limites desconhecidos/inesperados. Limitar bytes de entrada e de saída, número de quadros, duração, memória/CPU, tempo total e processos; tratar decoder travado e apagar materiais privados em sucesso, falha e cancelamento. Testar com entradas adversariais e falhas reais.
- Separar resultado extraído de inferência; apenas conectar modelo explicitamente consentido e devidamente isolado, com escopo claro, sem alegar conteúdo ou transcrição não observados. Não subir amostras sem consentimento específico. Nenhuma tarefa proposta deve ser executada automaticamente.
- Não ligar chat ou backend antes de autenticação, autorização, confirmação e isolamento adequados. Fallback continua fechado nas plataformas onde as garantias não forem possíveis.
