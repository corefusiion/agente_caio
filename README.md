<div align="center">
  <img src="docs/caio-banner.png" alt="Caio, assistente pessoal: suas ideias e conversas, do seu jeito" width="100%" />
</div>

# Caio

Um assistente pessoal em desenvolvimento, com uma interface de conversa e bases para recursos que ainda precisam ser integrados e validados. Este README distingue o que existe no código do que continua sendo plano — não é uma promessa de funcionamento em produção.

## Estado atual

| Área | O que existe e quais são os limites |
| --- | --- |
| **Chat e avatar** | O dashboard tem interface de chat conectada à API e usa o avatar aprovado na tela inicial e no cabeçalho. A operação depende de configuração e dos serviços disponíveis; não há garantia de funcionamento de ponta a ponta em toda instalação. |
| **Preferências locais** | O tamanho do texto das mensagens pode ser guardado no navegador. Há também uma biblioteca separada de preferências com consentimento explícito; ela **não está ligada ao chat**, não aprende com conversas e tem restrições de plataforma. |
| **Fila de tarefas** | Há um núcleo local e uma API opcional para um único operador, desligada por padrão. São um protótipo de registro de estados: não executam tarefas, não integram o chat nem comprovam aprovação ou identidade. O componente visual não está integrado à API. A base exige condições específicas de Linux/POSIX e não suporta Windows. |
| **Telegram** | Canal em desenvolvimento, limitado a texto e comandos de usuários autorizados em conversas privadas. Anexos recebidos não são analisados e saídas com anexos são recusadas. Conexão e entrega reais não foram validadas nesta revisão; **não ativar sem revisão adicional de segurança**. |
| **Compartilhamento de texto** | É possível colar texto ou link, revisar e acrescentar ao rascunho do chat antes de enviar. Não recebe vídeo nem analisa o conteúdo de um link e não é integrado ao menu de compartilhamento do celular. Para falar de um vídeo, forneça uma descrição ou transcrição. A instrução para não abrir links não impede tecnicamente o uso de ferramentas. |
| **Vídeo** | Existe apenas uma base isolada de planejamento com metadados informados pelo chamador. Não abre arquivos, extrai áudio ou imagens, transcreve, resume ou analisa vídeos reais; não está integrada à interface ou ao chat. |
| **Memória experimental** | Há um motor independente de compactação de conversa, mantido apenas em memória e sem integração com o chat ou persistência. Ele não implementa integração com Obsidian e não substitui a memória anterior do projeto. |
| **Agentes e integrações** | Há código e telas relacionados a agentes e serviços, mas suas capacidades e conexões de ponta a ponta não foram verificadas nesta revisão. Não trate indicadores ou a documentação histórica abaixo como prova de que estão ativos. |

### Privacidade e segurança

O histórico do chat pode ser armazenado localmente e as mensagens enviadas podem ser encaminhadas ao provedor configurado. A área de compartilhamento é uma prévia, não uma sessão privada. Evite inserir senhas, códigos, tokens, links privados e outros dados sensíveis. A interface ainda não oferece isolamento autenticado para múltiplos usuários. Não exponha serviços locais à internet sem uma revisão apropriada de autenticação, autorização e segurança.

## Próximos passos

- Validar chat, configuração e interface em ambientes reais, inclusive Windows, sem apresentar estados não verificados como conectados.
- Integrar a fila somente depois de identidade, autorização, aprovações e execução segura; testar a experiência completa.
- Revisar e testar o Telegram com conexões reais e limites de segurança antes de habilitá-lo.
- Evoluir compartilhamento e análise de vídeo com consentimento, isolamento, limites de recursos e testes adversariais, antes de receber mídia real.
- Planejar persistência e integração da memória com privacidade, controle do usuário e isolamento entre conversas.
- Verificar agentes e integrações reais antes de anunciá-los como disponíveis.

---

<details>
<summary>Documentação original preservada — histórica, não validada</summary>

> **Atenção:** o conteúdo original abaixo foi mantido para consulta histórica. Pode conter descrições aspiracionais, instruções desatualizadas e afirmações não verificadas (inclusive sobre memória, segurança, agentes, desempenho e integrações). Não use essas afirmações como status atual nem como garantia de operação ou segurança. Consulte o estado atual acima e revise as instruções antes de executá-las.

<div align="center">
  <img src="docs/mascote_original_hd.png" alt="Mascote Agente Caio" width="420"/>
  <h1>🛸 CaioCore v5.2 (Sovereign Intelligence)</h1>
  <p><strong>A Solução Suprema para Operações Inteligentes e Framework Unificado de IA</strong></p>
  <p>
    <img src="https://img.shields.io/badge/Versão-Premium_v5.2-violet?style=for-the-badge&logo=spaceship" alt="Versão Premium">
    <img src="https://img.shields.io/badge/Status-Soberano-purple?style=for-the-badge" alt="Status">
    <img src="https://img.shields.io/badge/Criado_por-Gleisson_Santos-blue?style=for-the-badge" alt="Autor">
  </p>
</div>

---

## ⚡ A Evolução: CaioCore v5.2 (A Onipresença Cognitiva)

Deixamos para trás a era dos multi-agentes fragmentados. Nesta evolução massiva, o **CaioCore** se consolidou como o *Mestre dos Magos*, aglomerando as habilidades de dezenas de especialistas focados (Vendas, Design, Automação, Programação, E-mails e Monitoramento) em **um único super-agente inteligente e soberano**. 

Diferente de frameworks tradicionais, o Caio v5.2 introduz o **Arsenal de Habilidades (Command Center)**, permitindo orquestração modular de ferramentas em tempo real com raciocínio encadeado (CoT).

---

### 🏗️ Arquitetura do Ecossistema (High-Level)

O CaioCore foi desenhado com alto poder de orquestração interna e loops refinados de observabilidade em Tempo Real.

```mermaid
graph TB
    %% Definitions
    classDef user fill:#f8fafc,stroke:#6366f1,stroke-width:2px,color:#1e293b
    classDef frontend fill:#0f172a,stroke:#8b5cf6,stroke-width:2px,color:#fff
    classDef gateway fill:#1e1b4b,stroke:#a855f7,stroke-width:2px,color:#fff
    classDef orchestrator fill:#4c1d95,stroke:#c084fc,stroke-width:3px,color:#fff
    classDef tool fill:#064e3b,stroke:#10b981,stroke-width:1px,color:#fff
    classDef db fill:#18181b,stroke:#52525b,stroke-width:1px,color:#a1a1aa

    %% Nodes
    User([👤 Operador Soberano]):::user
    
    subgraph UI_Layer ["🎨 Camada de Interface (v5.2 Premium)"]
        Dashboard["💻 Dashboard React + Tailwind v3<br/>(Glassmorphism & Neural Sphere)"]:::frontend
        CLI["📟 Caio CLI v0.1.4"]:::frontend
    end

    subgraph Core_Engine ["🧠 Matriz de Processamento (Gateway)"]
        Router["🛸 API Gateway / FastAPI<br/>(Roteamento Inteligente)"]:::gateway
        Orchestrator["⚡ Mestre dos Magos<br/>(Loop de Raciocínio CoT)"]:::orchestrator
    end

    subgraph Arsenal ["⚔️ Arsenal de Habilidades (Modules)"]
        direction LR
        Files["📁 Sandbox FS"]:::tool
        Search["🌐 Web Search"]:::tool
        Mail["📧 Sentinel Mail"]:::tool
        MCP["🧩 MCP Host"]:::tool
    end

    subgraph Persistence ["💾 Memória e Conhecimento"]
        Obsidian[("📝 Obsidian Knowledge Graph<br/>(Neural Memory)")]:::db
        Vector[("🧠 PGVector DB<br/>(Embeddings)")]:::db
    end

    %% Connections
    User --> UI_Layer
    UI_Layer --> Router
    Router <--> Orchestrator
    Orchestrator --> Arsenal
    Orchestrator <--> Persistence
    
    %% Feedback
    Arsenal -.->|Data Feedback| Orchestrator
```

---

### 🔮 Sovereign UI & Cognitive Insight

O Dashboard v5.2 não é apenas uma interface de chat; é um **Centro de Operações Táticas (War Room)**:

*   **Real-Time Reasoning Panel**: Acompanhe o "Cadeia de Pensamento" (Chain of Thought) do Caio enquanto ele orquestra ferramentas, garantindo total transparência no processo decisório.
*   **Artifact Canvas**: Visualização instantânea de código, documentos e diagramas gerados pelo agente em uma área dedicada, sem poluir o fluxo do chat.
*   **Neural Sphere Monitoring**: Feedback visual da atividade neural do sistema, indicando carga de processamento e saúde dos agentes de Tier 1.

---

### 🧠 Memória Evolutiva (Integração Automática com Obsidian)

Uma das maiores revoluções do CaioCore é como ele lida com a retenção de aprendizado prolongado através de uma **Matriz de Memória Híbrida**:

```mermaid
graph LR
    %% Styles
    classDef input fill:#6366f1,stroke:#4f46e5,color:#fff
    classDef process fill:#1e1b4b,stroke:#8b5cf6,color:#fff
    classDef storage fill:#18181b,stroke:#10b981,color:#fff
    classDef view fill:#0f172a,stroke:#3b82f6,color:#fff

    In[[User Prompt]]:::input --> Core[CaioCore Engine]:::process
    Core -->|Processamento AST| AST[Neural Mapping]:::process

    subgraph Brain ["Matriz Cognitiva"]
        AST -->|Embeddings| Vector[("PGVector DB<br/>(Semântica)")]:::storage
        AST -->|Markdown 3D| Obsidian[("Obsidian Graph<br/>(Documentação)")]:::storage
    end

    Obsidian -->|Visualização| GraphView["🧠 3D Knowledge Map"]:::view
    Vector -->|RAG| Core
```

1. **Economia Imediata**: Em interações curtas ou de longo prazo, gastamos até **70% menos tokens**, pois a IA não precisa reler manuais absolutos. Ela extrai blocos de memória apenas quando necessário.
2. **Obsidian 3D**: Agora você não apenas lê logs num terminal preto; todo o mapa cerebral do Caio atualiza um repositório `.md` automaticamente. Com o **Obsidian**, você visualiza o cérebro dele em Grafos 3D!
3. **Persistência Total**: A memória inteligente e rastreada é infalível e persistente, sobrevivendo a reinicializações e atualizações de infraestrutura.

---

### ⚔️ Arsenal de Habilidades (Modules v5.2)

O Caio v5.2 não apenas conversa; ele **executa**. O Command Center permite ativar módulos específicos para cada missão:

*   **📁 Filesystem Sandbox**: Edição e criação de arquivos em ambiente isolado e seguro.
*   **🌐 Web Search (Deep Search)**: Busca profunda em tempo real via Brave/Google API para dados atualizados.
*   **📧 Sentinel Mail**: Gestão inteligente de e-mails (IMAP/SMTP) com transformadores de visualização em cards.
*   **🧩 MCP Host (Model Context Protocol)**: Integração universal com qualquer ferramenta compatível com o protocolo MCP.
*   **⏰ Orquestrador de Cron**: Agendamento de tarefas complexas e gestão de calendário Google nativa.

---

### 🔥 Comparativo de Tecnologias

Abaixo, veja o que o **CaioCore** faz em comparação à base de outros frameworks famosos no ecossistema:

| Funcionalidade Estratégica | 🛸 CaioCore (Mestre dos Magos) | 🤖 Nanobot / CaioBot | 🦁 OpenClaw |
| :--- | :--- | :--- | :--- |
| **Identidade do Agente** | **Universal Onipotente (Tudo em 1)** | Multi-agentes isolados, perda de contexto ao trocar | Focado apenas em Dev de Software/Terminal |
| **Interface Nativa / UI** | Dashboard Moderno **React + Tailwind v3 + Shadcn**, zero lag de chat e responsivo | Linhas de comando feias ou WebView HTML estático | Apenas Prompt e Shell puro |
| **Processamento de E-mails** | Busca IMAP robusta. Transforma textos sujos em **Cards visuais limpos no Frontend** | Não possui / Precisa de plugin limitador | Não possui capacidade de caixa de entrada nativa |
| **Gestão de Custos** | Sistema embutido de Observabilidade (**`TokenAgent`**) para monitorar gastos precisos por IA | Sem rastreio consolidado | Limitado a logs perdidos no terminal |
| **Roteamento Smart** | Roteia dinamicamente LLMs de acordo com o peso da requisição (Gemini, Groq, Claude) | Configuração travada por sistema | Seleção travada para o modelo mais caro (Claude) |

<div align="center">
  <img src="docs/dashboard.png" alt="Dashboard Moderno CaioCore - Tailwind & TS" width="800"/>
  <p><i>Novo Painel Central CaioCore: Minimalista, UI Responsiva React/Vite, Tracker Visual Integrado</i></p>
</div>

---

## 🛠️ Guia Completo de Instalação (Local)

Para desenvolver ou auditar e rodar na sua máquina com Windows/Linux/Mac:

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/gleisson-santos/agente_caio.git
   cd agente_caio
   ```
2. **Sistema Virtual (VENV):** Recomendamos utilizar Python 3.11+.
    ```bash
    python -m venv .venv
    # Ative o ambiente local
    source .venv/bin/activate  # Linux/Mac
    .venv\Scripts\activate     # Windows
    ```
3. **Instale as dependências modulares:**
   Escolha a profundidade da sua versão do Caio:
   - *Padrão (Core):* `pip install -e .`
   - *Browser/Visão:* `pip install -e ".[browser]"` -> *(Instala a visao computacional e Playwright para "assistir" scraping).*
   - *Extremo (Todas APIs):* `pip install -e ".[all]"`

### Configurando o Coração (`config.json` e `token.json`)

Para gerar as conexões e habilitar as integrações autônomas do Caio (Telegram, Agenda e Imap):
Copie `config.example.json` para `config.json` e insira suas predefinições.

- **Para o Gmail (IMAP/SMTP):** O uso obrigatório de Senhas de App do Google (não use sua senha mestra).
- **Para o Google Calendar:** Rode o utilitário nativo executando `python tools/gerar_token_google.py`. Faça login no navegador pop-up e o arquivo autenticado `token.json` será criado com perfeição para uso eterno.

### Dando Partida Rápida
```bash
caio gateway
```
✅ **Tudo on-line!** O `caio gateway` acorda as rotas do backend (Porta `18790`) em multi-janela assíncrona blindada, já levantando rastreamento pronto pro Frontend (Vite) conectar sem CORS.

---

## 2. Produção Elite: Deploy em VPS (Docker Swarm / Portainer)

Preparado pro mundo real com balanceamento de nós em Swarm, proxies dinâmicos do Traefik, separação violenta do Banco Vetorial e de uso. 
📋 **Leia a Bíblia de Infra:** ➡️ [**DEPLOY_VPS.md**](DEPLOY_VPS.md) ⬅️

---

## 📢 Melhorias Realizadas (CaioCore Evolution) 💥

### 🛸 v5.2 — Sovereign Intelligence
*   **Neural Memory Graph**: Integração profunda com Obsidian via AST para visualização 3D do conhecimento.
*   **Arsenal de Habilidades**: Interface de comando modular (Command Center) para ativação de ferramentas on-the-fly.
*   **Real-Time Reasoning (CoT)**: Painel de pensamento visível, permitindo auditar o raciocínio da IA antes da resposta final.
*   **Performance Zero-Lag**: Refatoração completa do motor de renderização do Dashboard para suportar streams massivos sem perda de FPS.

### ⚡ v4.0 — Premium UI/UX
*   **Remoção das Bordas UI**: Design "Borderless" Premium para foco total na produtividade.
*   **Latência de React (Eliminada)**: Uso intensivo de `useCallback` e `memo` para digitação fluida.
*   **Integração TokenAgent**: Monitoramento de custos em tempo real diretamente no painel principal.
*   **Cards de E-mail**: Visualização modular de e-mails Sentinel via Tailwind Pro.

---

## 🤝 Créditos e Contato

**Arquiteto/Desenvolvedor Chefe:** Gleisson Santos  
**Ecossistema Principal/Deploy:** Plataforma UomniMind  

<div align="center">
  <p><strong>Agente Caio — A Onipresença Cognitiva da Automação.</strong></p>
</div>

</details>
