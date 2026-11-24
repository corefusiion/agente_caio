# Download de vídeo — política offline, ainda indisponível

**Não baixa nem abre vídeos.** O pacote separado `caiocore.video_download` faz apenas uma triagem de sintaxe e autorização offline; não está ligado ao chat, API ou canais. Não há executor, porta de download, subprocesso, DNS, HTTP, yt-dlp, cookies ou configuração para ativar downloads. Não gera arquivo, transcrição nem resumo. `yt-dlp` não foi encontrado no PATH ou como módulo Python do ambiente inspecionado; isso não comprova o estado de outras instalações ou do Windows.

A seleção estrita reconhece apenas alguns formatos HTTPS de YouTube (watch com somente `v`, shorts e youtu.be com ID direto), Instagram (reel/p) e TikTok (`@usuario/video/id`). Links de compartilhamento com rastreadores, parâmetros adicionais, fragmentos, encodings, portas explícitas, outras rotas e redirecionadores são recusados, mesmo quando legítimos. URL sintaticamente válida pode apontar para conteúdo privado, pago, indisponível, removido ou não ser vídeo. Nenhuma checagem de visibilidade foi feita, nem há acesso a conteúdo privado, bypass de restrições ou paywalls.

## Contrato

```python
from caiocore.video_download import assess

class PoliticaLocal:
    def authorize(self, candidate):
        # Exemplo apenas: política real precisa ter contexto de identidade e escopo.
        return True

r = assess('https://youtu.be/abcdefghijk', consent=True,
           rights_confirmed=True, authorizer=PoliticaLocal())
assert r.status == 'download_unavailable'
assert not r.download_performed and not r.visibility_verified
```

A autorização injetada é **código confiável do integrador**, não é sandbox: deve ser local, sem rede, arquivos ou logs sensíveis e verificar usuário e permissão em seu próprio contexto. A aplicação não deve oferecer uma implementação controlada pelo usuário. O adaptador não pode limitar os efeitos de uma porta maliciosa. Consentimento e declaração de direitos precisam ser o booleano `True` exato; o retorno da porta também. Negação, exceção, ausência de porta ou declaração falham fechadas; erros não são registrados nem propagados. Não são guardados nem devolvidos URLs ou IDs em `Assessment`, e o ID não aparece no `repr` de `Candidate`. O identificador passa à porta e deve ser tratado como sensível. Uma declaração de direitos não prova titularidade nem publicidade.

## Antes de qualquer implementação real

- Instalar e fixar versão do yt-dlp apenas após revisão e aprovação, respeitando condições das plataformas e os direitos do conteúdo. Nunca tentar cookies, credenciais, autenticação, DRM ou contornar paywalls/restrições; abortar em vez de buscar alternativa.
- Um filtro inicial de URL não basta: o yt-dlp, extratores, redirecionamentos, playlists, manifestos HLS/DASH, legendas, proxies e processos auxiliares podem acessar outros hosts/arquivos. Criar worker isolado e gateway de saída obrigatório, independente do processo analisado, sem acesso direto ou indireto à rede local, loopback, link-local, metadata/cloud, IPv6 interno, socket Unix sensível ou DNS não controlado. Não depender de allowlist da URL original ou opções de CLI como isolamento.
- Validar cada destino e cada redirecionamento, resolver e filtrar **todos** os endereços, conectar ao endereço público efetivamente validado sem nova resolução (defesa a DNS rebinding/TOCTOU), manter TLS e hostname verificados, e revalidar cada nova conexão/requisição inclusive CDNs e manifests. Negar IPs não globais, esquemas/portas indevidos e ambiguidades; manter lista restrita e auditada das origens necessárias ou negar. Não existe demonstração disso aqui.
- Isolar processo/árvore, sistema de arquivos, credenciais e descriptors, com usuário sem privilégios, ambiente limpo sem proxy/configs/plugins/cookies herdados, diretório privado, quota, tratamento seguro de symlink/hardlink e limpeza em sucesso, falha e cancelamento. Não usar shell nem registrar URLs, tokens, stdout/stderr sensíveis; sanitize erros. Desabilitar playlists e saídas/auxiliares não necessários.
- Aplicar limites reais e independentes de tempo de parede (inclusive encerrando toda a árvore), bytes transferidos e gravados durante streaming, quantidade de solicitações/redirecionamentos, CPU, memória e disco; testar respostas sem tamanho declarado, oversize e workers travados. Limites não são implementados neste adaptador.
- Verificar o isolamento no ambiente de produção com testes adversariais reais e sem dependência de comportamento voluntário do yt-dlp. Linux e Windows precisam de mecanismos e validações próprios; não há garantia de paridade nem suporte funcional em nenhum deles. Só conectar à interface após validação independente e manter fechado onde não for possível.

## Testes

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_video_download_policy.py`

Os mocks demonstram que este módulo e a porta simulada não executam processos nem acessam arquivos/DNS/rede. Não testam um yt-dlp real, DNS rebinding, redirecionamentos, limites de recursos ou isolamento de rede; esses riscos permanecem bloqueadores. O código não altera `caiocore.video_analysis`.
