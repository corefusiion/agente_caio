# Instalação do Caio: diagnóstico primeiro

Este é um roteiro de preparação, **não um instalador pronto nem uma certificação de segurança**. O diagnóstico é offline, somente leitura e não inicia serviços, instala pacotes, executa comandos externos, consulta contas ou lê arquivos de configuração/credenciais. Usa a biblioteca padrão do Python; não imprime caminhos de ferramentas nem valores do ambiente. Leia os resultados antes de autorizar qualquer instalação ou integração.

## 1. Na cópia revisada do projeto

Abra um terminal na raiz da cópia do Caio. É necessário Python 3.8+ para executar o diagnóstico; o próprio projeto declara Python **3.11 ou superior**. Se Python não estiver disponível, o diagnóstico não consegue rodar: obtenha-o pelos canais oficiais e verifique antes de instalar. No Windows, o launcher `py` é uma opção; se não existir, use `python` correspondente. No Linux, use `python3`:

```powershell
# Windows, PowerShell
py -3 -B scripts\caio_diagnostico.py
```

```sh
# Linux
python3 -B scripts/caio_diagnostico.py
```

`-B` evita escrita de bytecode. O diagnóstico informa o sistema, a versão do interpretador, a **presença no PATH** de Git, Node, npm e Docker, e se metadados das dependências Python diretas do `pyproject.toml` são encontrados nesse interpretador. Se Python for anterior a 3.11, não lê os requisitos, por falta do `tomllib` padrão. Node/npm são relevantes para construir a interface; Docker é uma alternativa, não obrigatório para o diagnóstico. Ferramentas ausentes podem ser normais antes de instalar.

Não são conferidos versões ou execução de Git/Node/npm/Docker, plugin Compose, daemon, versões/compatibilidade de pacotes, extras, dependências transitivas, conteúdo do build, integridade, segurança ou configuração. Uma dependência detectada não necessariamente funciona. Códigos: 0 significa somente que a coleta possível terminou (pode haver avisos/faltas); 1 indica falha ao ler os requisitos em Python 3.11+. Uma versão de Python acima das classificadas pelo projeto não comprova compatibilidade. Se o diagnóstico não funcionar, não prossiga às cegas.

## 2. Preparar um ambiente Python isolado (opcional)

Isto cria `.venv` dentro do projeto; não roda o agente. Faça só após revisar e autorizar alterações locais, sem sobrescrever um ambiente existente:

```powershell
# Windows
py -3 -m venv .venv
.\.venv\Scripts\python.exe -B scripts\caio_diagnostico.py
```

```sh
# Linux
python3 -m venv .venv
./.venv/bin/python -B scripts/caio_diagnostico.py
```

Se a criação falhar, pare e consulte as instruções oficiais do Python/sistema para suporte a `venv`; não presuma que funcionou. O segundo diagnóstico examina esse novo ambiente, não o Python global. Caso decida instalar depois, a instalação de dependências pode precisar de rede e executar código de terceiros; revise fontes e versões, faça backup e autorize explicitamente antes. Os comandos abaixo **não foram executados por este diagnóstico**:

```powershell
# Windows: somente após revisão e autorização
.\.venv\Scripts\python.exe -m pip install -e .
```

```sh
# Linux: somente após revisão e autorização
./.venv/bin/python -m pip install -e .
```

Confira o código de saída e repita o diagnóstico com o Python do ambiente. Isso não valida o dashboard ou o agente completo. Os antigos `install.bat` e `install.sh` fazem instalação/upgrade, copiam configuração e podem acionar setup; não são um diagnóstico e sua mensagem final não prova que todas as etapas deram certo. Não os execute sem revisão/autorização.

## 3. Interface e servidor

O dashboard tem viewport e regras CSS para telas menores, mas isso não prova que todos os fluxos sejam responsivos ou utilizáveis no celular. A presença de `dashboard/dist/index.html` apenas indica um artefato, que pode estar antigo. Construir o frontend exige Node/npm e dependências; construção e operação precisam ser verificadas separadamente. `npm run dev` isolado não é o modo seguro de conectar ao agente.

O Compose atual tem perfil `agent` explícito e publica as portas em `127.0.0.1`; o proxy da interface foi desenhado apenas para uso local e **não tem autenticação individual**. Não publique portas na LAN/Internet nem coloque esse proxy atrás de proxy público. Em servidor remoto, até mesmo o acesso local requer uma estratégia própria de administração segura; acesso de celular ou de outros usuários precisa de arquitetura separada, com autenticação individual, TLS e controles adequados, implementada e testada. Não basta liberar uma porta, usar IP do servidor ou apontar um domínio. Portanto ainda não há um caminho remoto seguro pronto para celular. `localhost` no celular aponta para o próprio celular, não para o PC/servidor.

Antes de qualquer execução ou implantação, revise configuração efetivamente usada, proteja segredos, desative integrações não autorizadas, revise limites de segurança e persistência e faça testes isolados. O gateway pode iniciar canais e tarefas. O Compose não foi executado por este diagnóstico; depende de configuração real revisada e não deve ser usado como Swarm. Veja `docs/INTERFACE_SEGURA.md` para os requisitos e limitações conhecidas (incluindo persistência de agenda e falhas de escrita), e `docs/VERIFICACAO_REAL.md` para o escopo dos testes existentes. O guia antigo `DEPLOY_VPS.md` sugere Swarm/Traefik e exposição: **não o siga para esta configuração**.

## 4. Testar o diagnóstico

Com Python 3.11+ na raiz, rode os testes da biblioteca padrão sem instalar pacotes:

```powershell
# Windows
py -3 -B -m unittest discover -s tests -p test_caio_diagnostico.py -v
```

```sh
# Linux
python3 -B -m unittest discover -s tests -p test_caio_diagnostico.py -v
```

Os testes usam simulações para partes do diagnóstico; passar neles não comprova instalação real ou funcionamento do servidor.

## 5. Celular e validação posterior

Até existir aplicativo nativo, a intenção é uma interface web adaptável. Valide em navegador com larguras de celular (por exemplo 360 e 390 px): abertura de menu, navegação, campo de texto/teclado, rolagem, mensagens longas, SSE e reconexão, acessibilidade e ausência de vazamento de segredos. Esses testes não demonstram por si só segurança de acesso remoto. Integrações reais, execução de Docker, instalação e conexão de provedores exigem autorização e testes próprios. A página atualmente referencia fontes Google: carregá-la pode gerar acesso à rede; o diagnóstico não abre a página.
