# Segurança revisada: teste local, não produção

**Estado:** estas mudanças são apenas arquivos locais do repositório. Não indicam que alguma instância esteja instalada, desligada, atualizada ou segura. Não houve deploy nem validação com contas reais. A autorização dos endpoints e dos canais pode depender de outras alterações de código ainda em revisão; não trate este documento como confirmação de que elas já estão prontas.

O `docker-compose.yml` é para **Docker Compose local**, não para `docker stack deploy`/Swarm. Não copie as configurações antigas de `DEPLOY_VPS.md` ou `README.md`: podem publicar portas, adicionar rotas Traefik e montar segredos. Não alteramos stacks existentes. Os dois workflows agora publicam imagens apenas por acionamento manual; um deles não faz mais SSH/deploy. Mudanças no repositório não desativam workflows ou serviços em uma cópia já implantada. As tags `latest` não garantem versão nem procedência: para produção, revise código, dependências, imagem e use digest imutável após aprovação.

## O que muda por padrão

- Dashboard local: `127.0.0.1:8080`; gateway somente com perfil `agent`: `127.0.0.1:18790`. Não há portas bindadas em todas as interfaces. Labels indicam `traefik.enable=false`; nenhuma rota Traefik é configurada.
- O gateway usa imagem construída do código local e usuário 10001:10001, sem privilégios adicionais e com capacidades descartadas. Dashboard usa imagem Nginx sem root, na porta interna 8080. O build em si ainda usa root em etapas de imagem. Não há mount do diretório `caio-stack`, socket Docker, credenciais Google ou da pasta inteira de segredos.
- O workspace fica em volume nomeado; um novo volume recebe a propriedade inicial preparada na imagem. Volumes existentes podem precisar de ajuste cuidadoso de propriedade. Outros dados em `~/.caiocore`, como sessões de bridge ou agendamentos fora do workspace, **não são persistidos por este Compose**; não presuma que tarefas sobrevivam a recriações.
- O arquivo de configuração é montado somente para leitura. Recursos de alteração de configurações pelo dashboard podem falhar: altere o arquivo protegido no host e recrie o serviço, sem expor seu conteúdo. Algumas funções que tentem escrever em `/app`, navegador/Chromium ou bridge podem precisar de ajustes para execução sem root; não as consideramos testadas.

## Preparação local em Linux com Compose

Estes passos não foram executados nem validados no Windows. Use apenas máquina e conta de teste confiáveis; revisar o código público e seus scripts/dependências antes de fazer build. Não use cópia com credenciais reais até autorizações e isolamento serem testados.

1. Na pasta do projeto, crie o arquivo de configuração fora do contexto de build, sem sobrescrever o existente. Antes de qualquer build, revise todo o contexto (incluindo subpastas `bridge`, `caiocore`, `tools` e `dashboard`) e remova credenciais e dados sensíveis: o `.dockerignore` existente não cobre necessariamente todos os arquivos secretos e contextos podem ser enviados ao builder. O comando desativa explicitamente todos os canais presentes no exemplo, inclusive Telegram e e-mail, que podem vir ativados; restringe o workspace como defesa adicional, **não como sandbox**:

```sh
umask 077
mkdir -p ../caio-config
chmod 700 ../caio-config
python3 - <<'PY'
import json
from pathlib import Path
c = json.loads(Path('config.example.json').read_text(encoding='utf-8'))
for channel in c.get('channels', {}).values():
    if isinstance(channel, dict):
        channel['enabled'] = False
c.setdefault('tools', {})['restrictToWorkspace'] = True
with open('../caio-config/config.json', 'x', encoding='utf-8') as f:
    json.dump(c, f, ensure_ascii=False, indent=2)
PY
```

Se já existir `../caio-config/config.json`, o comando falha sem sobrescrevê-lo. Faça backup seguro e revise-o manualmente; não imprima credenciais. Um arquivo inválido pode causar fallback para padrões, portanto valide o JSON, confira todos os canais e as opções aplicáveis na versão efetiva do código antes de iniciar. O exemplo pode conter placeholders: não são credenciais válidas.

2. Para o UID 10001 ler o arquivo sem torná-lo legível por todos, em Docker Linux convencional pode-se usar `sudo chown 10001:10001 ../caio-config/config.json && sudo chmod 600 ../caio-config/config.json`. Edite-o posteriormente com um método seguro que preserve permissões e proprietário; revise UID mapping/ACLs em Docker rootless ou user namespaces. Nunca adicione segredos ao Git; `.gitignore` não protege histórico, backups, imagens ou logs. A variável opcional `CAIO_CONFIG_FILE` aponta para outro arquivo existente; proteja-o igualmente e mantenha-o fora do contexto de build e do Git.

3. Verifique a configuração antes de subir, sem divulgar a saída caso ela contenha dados sensíveis:

```sh
docker compose --profile agent config --quiet
docker compose up --build -d caio-dashboard
```

O dashboard é apenas interface estática: sem gateway pode mostrar erros ou dados incompletos. Para iniciar o gateway **somente depois de revisar config, permissões, ferramentas e endpoints**, use `docker compose --profile agent up --build -d caio-agent`. Mesmo sem canais, o gateway pode executar heartbeat, tarefas agendadas e chamadas externas conforme a versão/configuração; monitore custos e efeitos antes de deixá-lo rodando. `docker compose --profile agent down` para os containers deste projeto, sem apagar o volume. Não execute `down -v` sem backup e confirmação.

Portas locais não são autenticação: outros processos/usuários locais podem alcançá-las, assim como containers na mesma rede Docker podem acessar a porta interna. Há ressalvas de publicação localhost em versões antigas do Docker, firewalls e configurações de rede: mantenha Docker atualizado e teste de outra máquina, sem assumir isolamento. Não habilite acesso por redes compartilhadas sem controle.

## Canais e credenciais: ativação explícita

Depois de revisar o código efetivo e testar a autorização, habilite **um canal por vez** no arquivo protegido com `enabled: true`. Defina as credenciais válidas obtidas nos respectivos provedores e uma lista restrita de identidades corretas (`allowFrom` onde aplicável); listas vazias já foram tratadas como acesso amplo em partes do projeto, portanto nunca dependa de vazio para negar acesso. Teste tanto uma identidade permitida quanto outra negada. Algumas integrações podem ter mecanismos próprios, grupos ou webhooks: confirme todos os caminhos, e desligue o canal se não conseguir demonstrar bloqueio. Para e-mail, revise envio automático e permissões da conta antes de ativar. Para WhatsApp, a bridge é opcional, usa sessão e token próprios; não exponha sua porta, proteja sessão/token e valide autenticação ponta a ponta. Este Compose não persiste a sessão da bridge por padrão.

Use credenciais de teste com escopo mínimo, nunca compartilhe valores em prints, commits, tickets ou logs. Se houver suspeita de exposição, revogue/rotacione nos provedores e investigue histórico e backups. Arquivos Google, tokens e outras integrações não são montados automaticamente; só disponibilize cada segredo estritamente necessário, após revisão de permissões. O exemplo ou documentação antiga não comprova validade ou segurança de credenciais existentes.

## Acesso remoto apenas após revisão manual

Não há proxy público pronto neste Compose. **Não exponha** se autenticação/autorização de API, eventos, configurações, ferramentas, webhooks e canais ainda não estiver implementada e testada; TLS sozinho não autentica usuários. Após revisão explícita, um administrador pode projetar um reverse proxy com TLS válido, autenticação forte e autorização para **todas** as rotas, incluindo streaming/WebSocket e operações de escrita; verifique CSRF, origens, rate limits, headers, logs sem segredos e bloqueio de acesso direto ao backend. Limite ainda o acesso por VPN/firewall quando possível. Autenticação só no dashboard não protege API. Traefik exige configuração manual revisada de rede e middleware, não apenas `traefik.enable=true`.

Teste de fora, sem autenticação e com usuários não autorizados, inclusive acesso direto à porta da API, antes de liberar. O frontend pode apontar diretamente para a porta 18790 em vez do proxy de mesma origem; revisar build/configuração do frontend e SSE é necessário antes de uma implantação remota. Não publique 18790 só para contornar esse detalhe. Não oferecemos aqui receita automática de proxy nem garantimos compatibilidade com Swarm.

## Limitações

Container sem root e binds locais reduzem superfície, mas **não garantem isolamento** contra código malicioso, execução de ferramentas, prompt injection, vazamento por rede, escape do container ou acesso de administrador Docker. Não há validação de build, execução, Chromium, integrações, canais ou autenticação real neste ambiente. Sempre revise e teste o resultado consolidado, inclusive mudanças feitas em paralelo no backend, antes de qualquer uso com dados reais.
