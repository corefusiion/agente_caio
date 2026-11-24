"""
Caio Gateway API — serves real monitoring agent data.

Routes:
  /api/status          — aggregate status of all agents
  /api/agents          — list all agents with status
  /api/agent/{id}/status — individual agent status
  /api/agent/token/stats — token consumption stats
  /api/agent/token/ranking — per-agent token ranking
  /api/agent/db/status — database agent status
  /api/agent/db/metrics — database metrics
  /api/agent/db/connections — connection details
  /api/agent/life/health — health matrix
  /api/agent/life/alerts — active alerts
  /api/agent/server/metrics — VPS metrics (CPU/RAM/Disk)
  /api/agent/heartbeat — POST heartbeat from external agent
  /events              — real-time event feed
  /api/notify          — send Telegram notification
  /api/extras/generate/{type} — run extra scripts
"""

import asyncio
import os
import subprocess
import signal
import psutil
from pathlib import Path as _Path
from datetime import datetime, timezone
import time
import uuid
from typing import Any, List, Optional

import json
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Depends, Request, Response
from caiocore.server.security import SecurityMiddleware
from pydantic import BaseModel, StrictBool, StrictInt, StrictStr
from loguru import logger

# Get logger (for pydantic, if needed, otherwise loguru is primary)
# logger = logging.get_logger(__name__) # Keeping loguru as primary logger

from caiocore.server.chat_handler import router as chat_router
from caiocore.server.a2a_server import router as a2a_router

app = FastAPI(title="Caio Gateway API", version="2.0.0")

app.include_router(chat_router)
app.include_router(a2a_router)

app.add_middleware(SecurityMiddleware)

# Store references to gateway components
_agent = None
_bus = None
_config = None
_cron = None
_channels = None
_doc_agent = None
_start_time = datetime.now(timezone.utc)


# ── Pydantic models ─────────────────────────────────────────────────

class NotificationRequest(BaseModel):
    title: str
    message: str
    type: str = "info"
    chat_id: Optional[str] = None


class SettingsUpdate(BaseModel):
    model: Optional[str] = None
    maxTokens: Optional[int] = None
    temperature: Optional[float] = None
    botName: Optional[str] = None
    telegramEnabled: Optional[bool] = None
    emailEnabled: Optional[bool] = None
    whatsappEnabled: Optional[bool] = None
    telegramToken: Optional[str] = None
    telegramAllowList: Optional[str] = None
    telegramNotifyChatId: Optional[str] = None
    emailUser: Optional[str] = None
    emailPass: Optional[str] = None
    smtpHost: Optional[str] = None
    smtpPort: Optional[int] = None
    evolutionBaseUrl: Optional[str] = None
    evolutionApiKey: Optional[str] = None
    evolutionInstance: Optional[str] = None
    providerKeys: Optional[dict[str, str]] = None
    providerBases: Optional[dict[str, str]] = None
    providerEnabled: Optional[dict[str, bool]] = None
    searchProvider: Optional[str] = None
    braveKey: Optional[str] = None
    tavilyKey: Optional[str] = None
    autonomous: Optional[bool] = None
    omnirouteCombo: Optional[str] = None





class HeartbeatRequest(BaseModel):
    agent: str
    status: str = "online"
    response_time: Optional[float] = None
    last_task: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────

def _get_registry():
    from caiocore.agents.registry import agent_registry
    return agent_registry


def _get_event_store():
    reg = _get_registry()
    return reg._event_store


def _uptime_str() -> str:
    delta = datetime.now(timezone.utc) - _start_time
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    hours = secs // 3600
    mins = (secs % 3600) // 60
    return f"{hours}h {mins}m"



# ── Core endpoints ──────────────────────────────────────────────────

@app.get("/api/chat/ping")
async def chat_ping():
    """Diagnóstico rápido do Web Chat."""
    return {
        "agent_available": _agent is not None,
        "model": getattr(_agent, 'model', 'desconhecido') if _agent else None,
        "status": "pronto" if _agent else "aguardando_inicialização",
    }


@app.get("/api/status")
async def get_status():
    """Aggregate status of all monitoring agents + services."""
    registry = _get_registry()
    life_agent = registry.get("agent-life")
    
    # Base count includes Caio CEO
    caio_online = 1 if _agent else 0
    online = caio_online
    total = 1 # Start with Caio CEO

    # Use health matrix if life agent is available
    if life_agent:
        health = life_agent.get_health_matrix()
        # LifeAgent health matrix now includes itself and all others
        online += sum(1 for a in health if a.get("status") in ("online", "running", "aguardando", "executando"))
        total += len(health)
    else:
        agents = registry.list_all()
        online += sum(1 for a in agents if a.get("status") in ("online", "running", "executando"))
        total += len(agents)

    # Token metrics
    token_agent = registry.get("agent-token")
    tokens_today = 0
    if token_agent:
        metrics = token_agent.get_metrics()
        tokens_today = metrics.get("tokens_total_today", 0)

    # Life alerts
    alerts = 0
    if life_agent:
        alerts = life_agent.get_metrics().get("alerts_active", 0)

    # Services status
    # ... (rest of the services logic remains same)
    services = []
    if _config and hasattr(_config, "channels") and _config.channels:
        tg = getattr(_config.channels, "telegram", None)
        if tg and tg.enabled:
            services.append({
                "id": "tg", "name": "Telegram",
                "status": "online", "uptime": _uptime_str(), "response": "~120ms",
            })
        email = getattr(_config.channels, "email", None)
        if email and email.enabled:
            services.append({
                "id": "email", "name": "Email",
                "status": "online", "uptime": _uptime_str(), "response": "~4s",
            })

    return {
        "status": "online",
        "agents_online": online,
        "agents_total": total,
        "tokens_today": tokens_today,
        "alerts": alerts,
        "uptime": _uptime_str(),
        "services": services,
    }

@app.get("/api/agents")
async def get_agents():
    """List all registered monitoring agents with real status."""
    registry = _get_registry()
    life_agent = registry.get("agent-life")
    
    live_agents = []
    if life_agent:
        # LifeAgent provides the most accurate status for both active and passive agents
        live_agents = life_agent.get_health_matrix()
        # Transform keys to match expected output (agent_id -> agent)
        for a in live_agents:
            # Check if agent_id exists before popping to avoid KeyError on repeat calls
            if "agent_id" in a:
                a["agent"] = a.pop("agent_id")
    else:
        live_agents = registry.list_all()

    # Add Caio (CEO) as virtual agent (the main orchestrator)
    caio = {
        "agent": "caio-ceo",
        "name": "Agente Caio",
        "role": "Orquestrador Central",
        "tier": 0,
        "status": "online" if _agent else "offline",
        "last_update": datetime.now(timezone.utc).isoformat(),
        "uptime": _uptime_str(),
        "metrics": {
            "model": getattr(_agent, "model", "unknown") if _agent else "unknown",
        },
    }


    # Add Specialist Agents data
    try:
        # Define the set of CORE specialists that should always appear if registered
        core_specs = ["spec-email", "spec-schedule"]
        
        for agent_id_to_check in registry.list_ids():
            if agent_id_to_check.startswith("spec-"):
                spec_agent = registry.get(agent_id_to_check)
                if spec_agent and hasattr(spec_agent, "get_status"):
                    status_data = spec_agent.get_status()
                    
                    # If it's a premium specialist (not in core_specs), we could mark it as premium
                    if agent_id_to_check not in core_specs:
                        status_data["is_premium"] = True
                    
                    live_agents.append(status_data)
        
    except Exception as e:
        logger.error(f"Error adding specialists: {e}")

    return [caio] + live_agents






@app.get("/api/agent/{agent_id}/status")
async def get_agent_status(agent_id: str):
    """Get status of a specific agent."""
    registry = _get_registry()

    # Handle Caio CEO
    if agent_id == "caio-ceo":
        return {
            "agent": "caio-ceo",
            "name": "Agente Caio",
            "role": "Orquestrador Central",
            "tier": 0,
            "status": "online" if _agent else "offline",
            "last_update": datetime.now(timezone.utc).isoformat(),
            "uptime": _uptime_str(),
            "metrics": {},
        }

    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return agent.get_status()


# ── Token Agent endpoints ───────────────────────────────────────────

@app.get("/api/agent/token/stats")
async def get_token_stats():
    """Token consumption statistics."""
    registry = _get_registry()
    agent = registry.get("agent-token")
    if not agent:
        raise HTTPException(status_code=404, detail="Token agent not running")
    return agent.get_stats()


@app.get("/api/agent/token/ranking")
async def get_token_ranking():
    """Per-agent token consumption ranking."""
    registry = _get_registry()
    agent = registry.get("agent-token")
    if not agent:
        raise HTTPException(status_code=404, detail="Token agent not running")
    return agent.get_ranking()


# ── Email Agent endpoints ───────────────────────────────────────────

@app.get("/api/agent/email/inbox")
async def get_email_inbox():
    """Get recent emails from the IMAP inbox cache."""
    if not _channels:
        return {"inbox": []}
    email_ch = _channels.get_channel("email")
    if not email_ch:
        return {"inbox": []}
    
    recent = email_ch.get_recent_emails(5)
    inbox = []
    for item in recent:
        inbox.append({
            "from": item.get("sender", "(Desconhecido)"),
            "subject": item.get("subject", "(Sem assunto)"),
            "priority": "normal",
            "time": item.get("time", ""),
            "read": False,
            "body": item.get("body", ""),
        })
    return {"inbox": inbox}


# ── Schedule Agent endpoints ──────────────────────────────────────────

@app.get("/api/agent/schedule/data")
async def get_schedule_data():
    """Get live cron jobs and upcoming Google Calendar events."""
    crons = []
    events = []
    metrics = {"activeCrons": 0, "eventsToday": 0}
    
    if _cron:
        jobs = _cron.list_jobs(include_disabled=True)
        for j in jobs:
            try:
                # Format lastRun and nextRun
                last_run = ""
                if j.state.last_run_at_ms:
                    last_run = datetime.fromtimestamp(j.state.last_run_at_ms / 1000).strftime("%H:%M")
                
                next_run = ""
                if j.state.next_run_at_ms:
                    next_run = datetime.fromtimestamp(j.state.next_run_at_ms / 1000).strftime("%H:%M")

                expr = j.schedule.expr if j.schedule.kind == "cron" else f"{j.schedule.kind}"
                crons.append({
                    "name": j.name,
                    "expr": expr,
                    "status": "active" if j.enabled else "stopped",
                    "lastRun": last_run,
                    "nextRun": next_run
                })
                if j.enabled:
                    metrics["activeCrons"] += 1
            except Exception as e:
                logger.error(f"Error parsing cron {j.name}: {e}")
                
    # Fetch upcoming Google Calendar events
    try:
        from caiocore.agent.tools.google_calendar import GoogleCalendarTool
        import json
        
        gc = _config.tools.google_calendar
        gc_tool = GoogleCalendarTool(
            credentials_path=gc.credentials_path or "credentials.json",
            token_path=gc.token_path or "token.pickle"
        )
        try:
            service = gc_tool._get_service()
            events_json = gc_tool._list_events(service, max_results=10)
            events_data = json.loads(events_json)
        except Exception as err:
            logger.warning(f"Google Calendar not authenticated or failed: {err}")
            events_data = {"events": []}
            
        now_date = datetime.now().astimezone().date()
        
        for e in events_data.get("events", []):
            start = e.get("start")
            title = e.get("summary", "Sem Título")
            
            time_str = ""
            urgent = False
            if start:
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    dt = dt.astimezone() # convert to local time
                    if dt.date() == now_date:
                        metrics["eventsToday"] += 1
                        time_str = dt.strftime("%H:%M")
                        
                        # Mark urgent if within 2 hours
                        diff = (dt - datetime.now().astimezone()).total_seconds()
                        if 0 <= diff <= 7200:
                            urgent = True
                    else:
                        time_str = dt.strftime("%d/%m %H:%M")
                except Exception:
                    time_str = start
            
            events.append({
                "title": title,
                "time": time_str,
                "location": str(e.get("location", "—")),
                "urgent": urgent
            })
    except Exception as e:
        logger.warning(f"Could not load Google Calendar tools: {e}")
        
    return {
        "metrics": metrics,
        "crons": crons,
        "upcomingEvents": events
    }





# ── BD Agent endpoints ──────────────────────────────────────────────

@app.get("/api/agent/db/status")
async def get_db_status():
    """Database agent status."""
    registry = _get_registry()
    agent = registry.get("agent-bd")
    if not agent:
        raise HTTPException(status_code=404, detail="BD agent not running")
    return agent.get_status()


@app.get("/api/agent/db/metrics")
async def get_db_metrics():
    """Database metrics (response time, queries, errors)."""
    registry = _get_registry()
    agent = registry.get("agent-bd")
    if not agent:
        raise HTTPException(status_code=404, detail="BD agent not running")
    return agent.get_metrics()


@app.get("/api/agent/db/connections")
async def get_db_connections():
    """Detailed connection status for each Supabase database."""
    registry = _get_registry()
    agent = registry.get("agent-bd")
    if not agent:
        raise HTTPException(status_code=404, detail="BD agent not running")
    return agent.get_connection_status()


# ── Life Agent endpoints ────────────────────────────────────────────

@app.get("/api/agent/life/health")
async def get_life_health():
    """Health matrix of all supervised agents."""
    registry = _get_registry()
    agent = registry.get("agent-life")
    if not agent:
        raise HTTPException(status_code=404, detail="Life agent not running")
    return {
        "matrix": agent.get_health_matrix(),
        "metrics": agent.get_metrics(),
    }


@app.get("/api/agent/life/alerts")
async def get_life_alerts():
    """Active and recent alerts."""
    registry = _get_registry()
    agent = registry.get("agent-life")
    if not agent:
        raise HTTPException(status_code=404, detail="Life agent not running")
    return agent.get_alerts()


# ── SSO Agent endpoints ─────────────────────────────────────────────

@app.get("/api/agent/server/metrics")
async def get_server_metrics():
    """VPS / server metrics (CPU, RAM, Disk, Network, Uptime)."""
    registry = _get_registry()
    agent = registry.get("agent-sso")
    if not agent:
        raise HTTPException(status_code=404, detail="SSO agent not running")
    return agent.get_detailed_metrics()


# ── Heartbeat endpoint ──────────────────────────────────────────────

@app.post("/api/agent/heartbeat")
async def post_heartbeat(req: HeartbeatRequest):
    """Receive heartbeat from an external agent."""
    event_store = _get_event_store()
    if event_store:
        event_store.emit(
            agent=req.agent,
            event_type="heartbeat",
            status=req.status,
            message=f"Heartbeat from {req.agent}: {req.status}",
        )
    return {"received": True, "agent": req.agent}


# ── Events endpoint ─────────────────────────────────────────────────

@app.get("/events")
async def get_events(
    limit: int = Query(50, ge=1, le=200),
    agent_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
):
    """Real-time event feed from all agents."""
    event_store = _get_event_store()
    if not event_store:
        return []
    return event_store.query(
        limit=limit,
        agent_id=agent_id,
        event_type=event_type,
    )

# ── Tracing stream endpoint (SSE) ───────────────────────────────────

@app.get("/api/tracing/stream")
async def tracing_stream():
    """Disabled: global tracing events have no authenticated session ownership.

    The gateway bearer token authenticates access to the API, not ownership of
    individual chat sessions. The current broadcaster sends raw events from
    every channel to every subscriber. A client-supplied session ID or a
    browser-side filter cannot safely fix that. Do not subscribe or stream
    until both ownership and server-side data minimization are implemented.
    """
    raise HTTPException(
        status_code=503,
        detail="Tracing stream disabled: session isolation unavailable",
        headers={"Cache-Control": "no-store"},
    )

# ── Tracing history endpoint ────────────────────────────────────────

@app.get("/api/tracing")
async def get_tracing_logs(limit: int = Query(50, ge=1, le=500)):
    """Retrieve recent AI tracing logs."""
    try:
        from caiocore.agent.tracer import AgentTracer
        # In a real setup, we might persist this instance in api.py, but for now we create reading instance
        workspace_dir = _Path(os.path.expanduser("~")) / ".caiocore" / "workspace"
        if not workspace_dir.exists():
            workspace_dir = _Path(os.getcwd())
            
        tracer = AgentTracer(workspace_dir)
        return {"traces": tracer.get_recent_traces(limit=limit)}
    except Exception as e:
        logger.error(f"Error fetching traces: {e}")
        return {"traces": [], "error": str(e)}


# ── Existing endpoints (notify, extras, tasks) ─────────────────────

# (tasks API moved to dedicated section below)

@app.post("/api/extras/generate/{script_type}")
async def generate_extra(script_type: str, background_tasks: BackgroundTasks):
    """Execute document generator scripts."""
    scripts = {
        "pdf": "generate_pdf.py",
        "pptx": "generate_pptx.py",
        "xlsx": "generate_xlsx.py",
        "docx": "generate_docx.py",
    }

    if script_type not in scripts:
        raise HTTPException(status_code=404, detail="Script type not found")

    # Check new path first, fallback to old
    script_name = scripts[script_type]
    new_path = os.path.join(os.getcwd(), "caiocore", "documentos", "generators", script_name)
    old_path = os.path.join(os.getcwd(), "caiocore", "extras", script_name)
    script_path = new_path if os.path.exists(new_path) else old_path

    if not os.path.exists(script_path):
        raise HTTPException(status_code=500, detail=f"Script file {script_name} missing")

    out_dir = os.path.join(os.getcwd(), "out")
    os.makedirs(out_dir, exist_ok=True)

    def run_script():
        try:
            logger.info("Docs: Running generator {}", script_path)
            subprocess.run([_sys.executable, script_path], check=True, cwd=os.getcwd())
            logger.info("Docs: Generator {} completed", script_name)
        except Exception as e:
            logger.error("Docs: Error running {}: {}", script_name, e)

    background_tasks.add_task(run_script)
    return {"message": f"Execution of {script_type} generator started in background"}


# ── Documents API ────────────────────────────────────────────────────

import sys as _sys
from pathlib import Path as _Path
import shutil

_DOCS_OUT = os.path.join(os.getcwd(), "out")
_DOCS_TEMPLATES = os.path.join(os.getcwd(), "caiocore", "documentos", "templates")

_DOC_ICONS = {
    ".pdf": "📕", ".pptx": "📊", ".xlsx": "📗", ".docx": "📘",
    ".md": "📝", ".txt": "📄", ".csv": "📋",
    ".html": "🌐", ".bat": "⚙️", ".sh": "⚙️"
}


@app.get("/api/documents")
async def list_documents():
    """List generated documents without modifying the filesystem."""
    docs = []
    
    # Check both the project local /out and the global agent workspace /out
    out_dir_local = _Path(_DOCS_OUT)
    out_dir_agent = _Path(os.path.expanduser("~")) / ".caiocore" / "workspace" / "out"
    
    # GET must not silently copy or move files: browsers can issue it without
    # an Origin header, including from an untrusted cross-site page.

    seen_files = set()
    
    # Process files from both directories
    for search_dir in [out_dir_local, out_dir_agent]:
        if search_dir.exists():
            for f in sorted(search_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.is_file() and not f.name.startswith(".") and f.name not in seen_files:
                    seen_files.add(f.name)
                    stat = f.stat()
                    ext = f.suffix.lower()
                    docs.append({
                        "name": f.name,
                        "icon": _DOC_ICONS.get(ext, "📄"),
                        "size": stat.st_size,
                        "sizeFormatted": f"{stat.st_size / 1024:.1f} KB" if stat.st_size < 1048576 else f"{stat.st_size / 1048576:.1f} MB",
                        "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "type": ext.lstrip("."),
                        "status": "ready",
                        "fullPath": str(f)
                    })
    
    # Sort final combined list by date
    docs.sort(key=lambda x: x["createdAt"], reverse=True)
    return docs

@app.get("/api/documents/templates")
async def list_templates():
    """List available document templates."""
    templates_dir = _Path(_DOCS_TEMPLATES)
    templates = []
    template_meta = {
        "contrato_prestacao.md": {
            "title": "Contrato de Prestação de Serviços",
            "description": "Modelo completo com cláusulas padrão",
            "icon": "⚖️", "category": "legal",
        },
        "procuracao.md": {
            "title": "Procuração",
            "description": "Instrumento particular de procuração",
            "icon": "📜", "category": "legal",
        },
        "relatorio_gerencial.md": {
            "title": "Relatório Gerencial",
            "description": "Estrutura com KPIs, análises e recomendações",
            "icon": "📊", "category": "empresarial",
        },
        "ata_reuniao.md": {
            "title": "Ata de Reunião",
            "description": "Registro de participantes, deliberações e encaminhamentos",
            "icon": "📋", "category": "empresarial",
        },
    }
    if not _doc_agent:
        from caiocore.documentos.engine import get_templates
        return get_templates()
    return _doc_agent.get_templates()


@app.get("/api/documents/templates/{template_id}")
async def get_template_detail(template_id: str):
    if not _doc_agent:
        from caiocore.documentos.engine import get_template_content
        return get_template_content(template_id)
    return _doc_agent.get_template_detail(template_id)


class DocGenerateRequest(BaseModel):
    type: str  # pdf, pptx, xlsx, docx
    title: str = ""
    description: str = ""
    template_id: str = ""


@app.post("/api/documents/generate")
async def generate_document(req: dict):
    """Generate a document (DocAgent quick flow)."""
    if not _doc_agent:
        raise HTTPException(status_code=503, detail="DocAgent not initialized")
    try:
        doc_type = req.get("type", "docx")
        result = _doc_agent.generate_quick(doc_type)
        return result
    except Exception as e:
        logger.error(f"Error generating document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/ai-generate")
async def ai_generate_document(req: dict, background_tasks: BackgroundTasks):
    """Generate a document using CAIO (IA) content + Engine formatting."""
    if not _agent or not _doc_agent:
        raise HTTPException(status_code=503, detail="Agents not initialized")

    prompt = req.get("prompt")
    format = req.get("format", "docx")
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    try:
        # 1. Ask Caio to generate the content
        # We use a specialized system prompt for Caio to generate clean Markdown
        system_instructions = (
            "Você é o Especialista em Documentos. Sua tarefa é gerar o CONTEÚDO de um documento em formato Markdown.\n"
            "Use cabeçalhos (# ##), listas, tabelas e parágrafos claros.\n"
            "NÃO use introduções conversacionais como 'Aqui está o documento'. Comece direto no título.\n"
            "IMPORTANTE: Se for um contrato, use cláusulas. Se for relatório, use seções de análise."
        )
        full_prompt = f"{system_instructions}\n\nDocumento solicitado: {prompt}"
        
        # Call CAIO (the main agent)
        ai_content = await _agent.process_direct(full_prompt, session_key="doc_gen")
        
        # 2. Use engine to create the real file from AI text
        from caiocore.documentos.engine import generate_from_ai_content
        doc_id = f"AI_{int(time.time())}"
        filepath = generate_from_ai_content(ai_content, doc_format=format, title=doc_id)
        
        return {
            "status": "success",
            "doc_id": doc_id,
            "filename": os.path.basename(filepath),
            "content_preview": ai_content[:200] + "..."
        }
    except Exception as e:
        logger.error(f"AI generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/from-template")
async def generate_from_template(req: dict):
    """Generate a document based on a template with AI filling it."""
    if not _agent or not _doc_agent:
        raise HTTPException(status_code=503, detail="Agents not initialized")

    template_id = req.get("template_id")
    format = req.get("format", "docx")
    if not template_id:
        raise HTTPException(status_code=400, detail="template_id is required")

    try:
        # 1. Get template content
        tpl = _doc_agent.get_template_detail(template_id)
        content = tpl["content"]

        # 2. Ask CAIO to fill the template with realistic data
        fill_prompt = (
            "Preencha o seguinte template de documento com dados fictícios, mas realistas e profissionais.\n"
            "Mantenha a estrutura Markdown. Substitua [PLACEHOLDERS] por informações sensatas.\n"
            f"TEMPLATE:\n{content}"
        )
        filled_content = await _agent.process_direct(fill_prompt, session_key="doc_tpl")

        # 3. Generate file
        from caiocore.documentos.engine import generate_from_ai_content
        doc_id = f"TPL_{template_id}_{int(time.time())}"
        filepath = generate_from_ai_content(filled_content, doc_format=format, title=doc_id)

        return {
            "status": "success",
            "doc_id": doc_id,
            "filename": os.path.basename(filepath)
        }
    except Exception as e:
        logger.error(f"Template generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _document_secure_open_supported():
    # Windows lacks the dir_fd/O_NOFOLLOW guarantees used below. Never fall
    # back to a path-based check followed by a separate open.
    return (_sys.platform.startswith("linux") and os.open in os.supports_dir_fd
            and all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")))


def _open_document(directory: str, filename: str):
    """Open a single-link regular file beneath directory without following links.

    Traverse every directory component using held directory descriptors, then
    return the already-open file descriptor. Nothing later reopens its pathname.
    """
    import stat

    if (not filename or filename in (".", "..") or "/" in filename
            or "\\" in filename or "\x00" in filename):
        raise OSError("Invalid document name")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(os.path.sep, flags)
    try:
        for part in os.path.abspath(directory).split(os.path.sep):
            if not part:
                continue
            next_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Not a single-link regular document")
            return fd, info
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(directory_fd)


from starlette.responses import FileResponse as _FileResponse


class _DocumentDownloadResponse(_FileResponse):
    """Serve the checked Linux descriptor, retaining FileResponse range support."""

    def __init__(self, fd, info, filename):
        # procfs' magic link names the held descriptor, not the original path.
        # Verify procfs exposes this exact inode; never fall back to the pathname.
        descriptor_path = f"/proc/self/fd/{fd}"
        proc_info = os.stat(descriptor_path)
        if (proc_info.st_dev, proc_info.st_ino) != (info.st_dev, info.st_ino):
            raise OSError("Descriptor filesystem unavailable")
        super().__init__(descriptor_path, filename=filename, stat_result=info)
        self._document_fd = fd

    async def __call__(self, scope, receive, send):
        try:
            # A pathsend implementation might reopen the path after we close the
            # descriptor. Force Starlette to read while the descriptor is held.
            scope = dict(scope)
            scope["extensions"] = {key: value for key, value in scope.get("extensions", {}).items()
                                   if key != "http.response.pathsend"}
            await super().__call__(scope, receive, send)
        finally:
            os.close(self._document_fd)


@app.get("/api/documents/{filename}/download")
async def download_document(filename: str):
    """Download a generated document via a verified open descriptor."""
    import errno

    if not _document_secure_open_supported():
        raise HTTPException(status_code=503, detail="Secure document download unavailable")

    directories = (_DOCS_OUT, os.path.join(os.path.expanduser("~"), ".caiocore", "workspace", "out"))
    for directory in directories:
        try:
            fd, info = _open_document(directory, filename)
        except OSError as exc:
            # Only a genuinely missing local path permits the original fallback.
            # Symlinks, hardlinks, invalid names, and access errors fail closed.
            if exc.errno == errno.ENOENT:
                continue
            raise HTTPException(status_code=404, detail="File not found") from exc
        try:
            return _DocumentDownloadResponse(fd, info, filename)
        except OSError as exc:
            os.close(fd)
            raise HTTPException(status_code=503, detail="Secure document download unavailable") from exc
        except BaseException:
            os.close(fd)
            raise
    raise HTTPException(status_code=404, detail="File not found")


@app.delete("/api/documents/{filename}")
async def delete_document(filename: str):
    """Delete a generated document."""
    file_path_local = os.path.join(_DOCS_OUT, filename)
    file_path_agent = os.path.join(os.path.expanduser("~"), ".caiocore", "workspace", "out", filename)
    
    target_path = file_path_local if os.path.exists(file_path_local) else file_path_agent
    
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(target_path)
    logger.info("Docs: Deleted {}", filename)
    return {"status": "deleted", "filename": filename}


@app.post("/api/notify")
async def send_notification(req: NotificationRequest):
    """Send a notification directly to Telegram via HTTP API."""
    import httpx

    target_chat_id = req.chat_id
    tg_token = None

    if _config and _config.channels and _config.channels.telegram:
        tg_cfg = _config.channels.telegram
        tg_token = tg_cfg.token
        if not target_chat_id:
            target_chat_id = tg_cfg.notify_chat_id or (
                tg_cfg.allow_from[0] if tg_cfg.allow_from else None
            )

    if not target_chat_id or not tg_token:
        raise HTTPException(
            status_code=400,
            detail=f"Missing chat_id ({target_chat_id}) or token ({bool(tg_token)})",
        )

    full_message = f"[NOTIFICACAO] *{req.title}*\n\n{req.message}"

    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": full_message,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp_data = resp.json()

        if resp.status_code == 200 and resp_data.get("ok"):
            logger.info(f"API: Telegram notification sent: {req.title}")
            return {"status": "sent", "chat_id": target_chat_id}
        else:
            # Remote error text is untrusted and can contain sensitive data.
            logger.error("API: Telegram rejected notification")
            raise HTTPException(status_code=502, detail="Telegram notification failed")
    except httpx.HTTPError:
        # HTTP exceptions can contain the Telegram URL, including the token.
        logger.error("API: HTTP error sending notification")
        raise HTTPException(status_code=502, detail="Telegram notification failed") from None


# ── Agent initialization ────────────────────────────────────────────

def _init_monitoring_agents(config):
    """Initialize and start all monitoring agents based on config."""
    import json
    from caiocore.agents.registry import agent_registry
    from caiocore.agents.events import EventStore
    from caiocore.agents.token_agent import TokenAgent
    from caiocore.agents.bd_agent import BDAgent
    from caiocore.agents.life_agent import LifeAgent
    from caiocore.agents.sso_agent import SSOAgent

    # Create shared EventStore
    event_store = EventStore()
    agent_registry.set_event_store(event_store)

    # Read raw config.json for monitoring section (Pydantic strips unknown fields)
    monitoring_cfg = {}
    supabase_conns = []
    try:
        from caiocore.config.loader import get_config_path
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                raw = json.load(f)
            agents_cfg = raw.get("agents", {})
            monitoring_cfg = agents_cfg.get("monitoring", {})
            supabase_conns = raw.get("supabase_connections", [])
    except Exception as e:
        logger.warning("Could not read monitoring config: {}", e)

    # Token Agent
    token_cfg = monitoring_cfg.get("token_agent", {})
    if token_cfg.get("enabled", True):
        token_agent = TokenAgent()
        agent_registry.register(token_agent)
        logger.info("Monitoring: TokenAgent registered")

    # BD Agent
    bd_cfg = monitoring_cfg.get("bd_agent", {})
    if bd_cfg.get("enabled", True):
        bd_agent = BDAgent(connections=supabase_conns)
        agent_registry.register(bd_agent)
        logger.info("Monitoring: BDAgent registered (connections: {})", len(supabase_conns))

    # Life Agent
    life_cfg = monitoring_cfg.get("life_agent", {})
    if life_cfg.get("enabled", True):
        interval = life_cfg.get("heartbeat_interval_s", 30)
        life_agent = LifeAgent(
            registry=agent_registry,
            heartbeat_interval=interval,
        )
        agent_registry.register(life_agent)
        logger.info("Monitoring: LifeAgent registered (interval: {}s)", interval)

    # SSO Agent
    sso_cfg = monitoring_cfg.get("sso_agent", {})
    if sso_cfg.get("enabled", True):
        interval = sso_cfg.get("metrics_interval_s", 10)
        sso_agent = SSOAgent(metrics_interval=interval)
        agent_registry.register(sso_agent)
        logger.info("Monitoring: SSOAgent registered (interval: {}s)", interval)

    return agent_registry


async def _start_agent_loops():
    """Start background loops for all registered agents."""
    from caiocore.agents.registry import agent_registry
    for agent_id in agent_registry.list_ids():
        agent = agent_registry.get(agent_id)
        if agent and hasattr(agent, "start"):
            await agent.start()
    logger.info("Monitoring: all agent loops started")


@app.on_event("startup")
async def on_startup():
    """Start monitoring agent loops in the background when FastAPI starts."""
    # We use a background task to ensure the API becomes responsive IMMEDIATELY
    # while agents connect to their respective services (Supabase, VPS, etc.)
    async def run_init():
        try:
            # Let the server bind and start accepting requests first
            await asyncio.sleep(2)
            await _start_agent_loops()
        except Exception as e:
            logger.error(f"Startup: Final agent loop initialization failed: {e}")

    await asyncio.to_thread(_start_local_text_worker)
    asyncio.create_task(run_init())


@app.on_event("shutdown")
async def on_shutdown_local_text_worker():
    global _local_text_worker
    worker, _local_text_worker = _local_text_worker, None
    if worker is not None:
        await asyncio.to_thread(worker.stop)


# Legacy task endpoints are disabled: they shared unowned data, could execute
# arbitrary agent work and could report success after persistence failure.
@app.api_route('/api/tasks', methods=['GET', 'POST'])
@app.api_route('/api/tasks/{task_id}', methods=['DELETE'])
@app.api_route('/api/tasks/{task_id}/execute', methods=['POST'])
def legacy_tasks_disabled(task_id: str = ''):
    raise HTTPException(status_code=503, detail='Legacy task API disabled')


# Deliberately one local operator, NOT an authenticated chat/user identity.
# It is only set by the gateway's own loopback-bound server entrypoint.
_local_queue_server_loopback = False
_LOCAL_QUEUE_OWNER = 'local_operator'
_local_text_worker = None


def _start_local_text_worker():
    """One opt-in worker, only in the same loopback gateway trust boundary."""
    global _local_text_worker
    if os.getenv('CAIO_LOCAL_TEXT_WORKER_ENABLED') != '1':
        return
    token = os.getenv('CAIO_API_TOKEN', '')
    path = os.getenv('CAIO_LOCAL_QUEUE_DIR', '')
    if (os.getenv('CAIO_LOCAL_QUEUE_ENABLED') != '1' or not _local_queue_server_loopback
            or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token)
            or not path or not os.path.isabs(path)):
        logger.error('Local text worker not started: unsafe or incomplete configuration')
        return
    try:
        from caiocore.task_queue import Queue
        from caiocore.task_queue.worker import LocalTextWorker
        worker = LocalTextWorker(Queue(path, _LOCAL_QUEUE_OWNER))
        worker.start()
        _local_text_worker = worker
    except Exception:
        # Do not disclose paths or contents and do not start a second worker.
        logger.error('Local text worker not started: storage or exclusive lock unavailable')



class LocalQueueCreate(BaseModel):
    model_config = {'extra': 'forbid'}
    description: StrictStr
    priority: StrictInt = 0


class LocalTextStatisticsCreate(BaseModel):
    model_config = {'extra': 'forbid'}
    text: StrictStr
    consent: StrictBool
    priority: StrictInt = 0


def _queue_for_local_operator(request: Request, response: Response):
    from caiocore.server.security import _hostname, _origin, _loopback, _secret_matches
    from caiocore.task_queue import Queue, StorageSecurityError
    import sqlite3

    response.headers['Cache-Control'] = 'no-store'
    if any(key.lower() == 'owner' for key in request.query_params):
        raise HTTPException(422, 'Client owner is not accepted')
    if os.getenv('CAIO_LOCAL_QUEUE_ENABLED') != '1' or not _local_queue_server_loopback:
        raise HTTPException(503, 'Local queue disabled')
    token = os.getenv('CAIO_API_TOKEN', '')
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise HTTPException(503, 'Local queue not configured')
    scope = request.scope
    headers = request.headers
    auth = headers.get('authorization', '')
    if not auth.startswith('Bearer ') or not _secret_matches(token, auth[7:]):
        raise HTTPException(401, 'Access denied')
    peer = (scope.get('client') or ('', 0))[0]
    server = (scope.get('server') or ('', 0))[0]
    host = _hostname(headers.get('host', ''))
    forwarded = {'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-forwarded-port', 'x-real-ip'}
    if (not _loopback(peer) or not _loopback(server)
            or host not in {'localhost', '127.0.0.1', '::1'}
            or any(name in headers for name in forwarded)):
        raise HTTPException(403, 'Access denied')
    origin = headers.get('origin')
    if origin is not None and (_origin(origin) is None or _origin(origin) != _origin(f"{scope.get('scheme', 'http')}://{headers.get('host', '')}")):
        raise HTTPException(403, 'Access denied')
    path = os.getenv('CAIO_LOCAL_QUEUE_DIR', '')
    if not path or not os.path.isabs(path):
        raise HTTPException(503, 'Local queue not configured')
    try:
        return Queue(path, _LOCAL_QUEUE_OWNER)
    except (StorageSecurityError, OSError, sqlite3.Error, ValueError):
        raise HTTPException(503, 'Local queue storage unavailable') from None


def _queue_call(func, *args, **kwargs):
    from caiocore.task_queue import InvalidTransition, StorageSecurityError
    import sqlite3
    try:
        return func(*args, **kwargs)
    except InvalidTransition:
        raise HTTPException(409, 'Task unavailable') from None
    except ValueError:
        raise HTTPException(422, 'Invalid task data') from None
    except (StorageSecurityError, OSError, sqlite3.Error):
        raise HTTPException(503, 'Local queue storage unavailable') from None


def _local_public(task):
    if task is None:
        return None
    return {k: v for k, v in task.items() if k != 'owner'}


@app.get('/api/local-queue/tasks')
def local_queue_list(status: Optional[str] = None, limit: int = 100, offset: int = 0,
                     queue = Depends(_queue_for_local_operator)):
    return [_local_public(x) for x in _queue_call(queue.list, status=status, limit=limit, offset=offset)]


@app.post('/api/local-queue/tasks', status_code=201)
def local_queue_create(body: LocalQueueCreate, queue = Depends(_queue_for_local_operator)):
    return _local_public(_queue_call(queue.create, body.description, body.priority))


@app.get('/api/local-queue/worker')
def local_queue_worker_status(queue = Depends(_queue_for_local_operator)):
    worker = _local_text_worker
    active = bool(worker and worker.running and worker.queue.directory == queue.directory)
    return {'running': active, 'kind': 'estatisticas_texto_local_v1',
            'error': worker.error if worker else None}


@app.post('/api/local-queue/text-statistics', status_code=201)
def local_queue_text_statistics(body: LocalTextStatisticsCreate, queue = Depends(_queue_for_local_operator)):
    if body.consent is not True:
        raise HTTPException(422, 'Explicit consent required')
    worker = _local_text_worker
    if (os.getenv('CAIO_LOCAL_TEXT_WORKER_ENABLED') != '1' or not worker
            or not worker.running or worker.queue.directory != queue.directory):
        raise HTTPException(503, 'Local text worker unavailable')
    return _local_public(_queue_call(queue.create_text_stats, body.text, body.priority, consent=True))


def _chat_queue_boundary(request: Request):
    # Do not accept the dashboard's bearer-injecting proxy or browser requests:
    # its unauthenticated session and proxy identity cannot prove the operator.
    if os.getenv('CAIO_LOCAL_CHAT_QUEUE_ENABLED') != '1':
        raise HTTPException(503, 'Local chat queue disabled')
    from caiocore.server.security import _secret_matches
    key = os.getenv('CAIO_LOCAL_CHAT_QUEUE_KEY', '')
    if (len(key) < 32 or not key.isascii() or any(c.isspace() for c in key)
            or _secret_matches(key, os.getenv('CAIO_API_TOKEN', ''))):
        raise HTTPException(503, 'Local chat queue not configured')
    supplied = request.headers.getlist('x-caio-local-chat-key')
    if len(supplied) != 1 or not _secret_matches(key, supplied[0]):
        raise HTTPException(401, 'Access denied')
    if request.query_params or request.headers.get('origin') is not None or request.headers.get('cookie') is not None:
        raise HTTPException(403, 'Direct local client required')
    if any(k.lower().startswith('sec-fetch-') for k in request.headers):
        raise HTTPException(403, 'Direct local client required')


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


@app.post('/api/chat/local-text-statistics')
async def chat_queue_text(request: Request, response: Response, queue=Depends(_queue_for_local_operator)):
    """Explicit local chat action; never calls the agent or trusts session IDs."""
    _chat_queue_boundary(request)
    if request.headers.get('content-type', '').lower() != 'application/json':
        raise HTTPException(415, 'JSON required')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 20000:
            raise HTTPException(413, 'Request too large')
    try:
        body = json.loads(raw, object_pairs_hook=_unique_json_object)
        if not isinstance(body, dict) or set(body) - {'request_id', 'text', 'consent', 'priority'}:
            raise ValueError()
        if set(body) < {'request_id', 'text', 'consent'}:
            raise ValueError()
        if type(body['request_id']) is not str or type(body['text']) is not str or body['consent'] is not True:
            raise ValueError()
        priority = body.get('priority', 0)
        if type(priority) is not int:
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise HTTPException(422, 'Invalid task data') from None
    worker = _local_text_worker
    if (os.getenv('CAIO_LOCAL_TEXT_WORKER_ENABLED') != '1' or not worker
            or not worker.running or worker.queue.directory != queue.directory):
        raise HTTPException(503, 'Local text worker unavailable')
    from starlette.concurrency import run_in_threadpool
    task, created = await run_in_threadpool(_queue_call, queue.create_chat_text_stats,
                                            body['request_id'], body['text'], priority, consent=True)
    response.status_code = 201 if created else 200
    return {'created': created, 'task': _local_public(task)}


@app.get('/api/chat/local-text-statistics/{task_id}')
def chat_queue_get(task_id: str, request: Request, queue=Depends(_queue_for_local_operator)):
    _chat_queue_boundary(request)
    task = _queue_call(queue.get, task_id)
    if task is None or task['kind'] != 'estatisticas_texto_local_v1':
        raise HTTPException(404, 'Task unavailable')
    return {'task': _local_public(task)}


@app.get('/api/local-queue/tasks/{task_id}')
def local_queue_get(task_id: str, queue = Depends(_queue_for_local_operator)):
    task = _queue_call(queue.get, task_id)
    if task is None:
        raise HTTPException(404, 'Task unavailable')
    return _local_public(task)


@app.post('/api/local-queue/tasks/{task_id}/cancel')
async def local_queue_cancel(task_id: str, request: Request, queue = Depends(_queue_for_local_operator)):
    if await request.body():
        raise HTTPException(422, 'Cancel does not accept a body')
    # SQLite may block on its transaction; do not block the event loop.
    from starlette.concurrency import run_in_threadpool
    return _local_public(await run_in_threadpool(_queue_call, queue.cancel_waiting, task_id))


@app.get('/api/local-queue/audit')
def local_queue_audit(limit: int = 100, after: int = 0, queue = Depends(_queue_for_local_operator)):
    return _queue_call(queue.audit, limit=limit, after=after)


# ── Settings API ─────────────────────────────────────────────────────

# Masked values are display-only. Empty/masked submissions do not clear secrets.
_SECRET_MASK = "********"

def _mask_secret(value):
    return _SECRET_MASK if value else ""

def _mask_sensitive_url(value):
    # Credentials can appear anywhere, including paths and hostnames.
    return _mask_secret(value)


def _new_secret(value):
    if value is None or not value.strip():
        return False
    stripped = value.strip()
    # Support common password widgets without echoing partial secrets.
    return not all(c in "*•●·" for c in stripped) and stripped.lower() not in {"[redacted]", "<redacted>", "[secret]"}


@app.get("/api/settings")
async def get_settings():
    """Get current agent configuration with robust fallbacks."""
    if not _config:
        return {
            "model": "gpt-4o",
            "maxTokens": 4096,
            "temperature": 0.7,
            "botName": "CaioAgent",
            "telegramEnabled": False,
            "emailEnabled": False,
            "whatsappEnabled": False,
            "providerKeys": {},
            "providerBases": {},
            "searchProvider": "brave",
            "braveKey": "",
            "tavilyKey": ""
        }
    
    # Safely extract values with getattr to prevent crashes if config structure is partial
    def safe_get(obj, path, default=None):
        for part in path.split("."):
            obj = getattr(obj, part, None)
            if obj is None: return default
        return obj

    return {
        "model": safe_get(_config, "agents.defaults.model", "gpt-4o"),
        "maxTokens": safe_get(_config, "agents.defaults.max_tokens", 4096),
        "temperature": safe_get(_config, "agents.defaults.temperature", 0.7),
        "botName": getattr(_config, "bot_name", "CaioAgent"),
        "autonomous": safe_get(_config, "agents.defaults.autonomous", True),

        "telegramEnabled": safe_get(_config, "channels.telegram.enabled", False),
        "emailEnabled": safe_get(_config, "channels.email.enabled", False),
        "whatsappEnabled": safe_get(_config, "channels.evolution.enabled", False),
        
        "telegramToken": _mask_secret(safe_get(_config, "channels.telegram.token", "")),
        "telegramAllowList": ",".join(safe_get(_config, "channels.telegram.allow_from", [])),
        "telegramNotifyChatId": safe_get(_config, "channels.telegram.notify_chat_id", ""),
        
        "emailUser": safe_get(_config, "channels.email.imap_username", ""),
        "emailPass": _mask_secret(safe_get(_config, "channels.email.imap_password", "")),
        "smtpHost": safe_get(_config, "channels.email.smtp_host", ""),
        "smtpPort": safe_get(_config, "channels.email.smtp_port", 587),

        "evolutionBaseUrl": _mask_sensitive_url(safe_get(_config, "channels.evolution.base_url", "")),
        "evolutionApiKey": _mask_secret(safe_get(_config, "channels.evolution.api_key", "")),
        "evolutionInstance": safe_get(_config, "channels.evolution.instance_name", ""),
        
        "providerKeys": {
            "openrouter": _mask_secret(safe_get(_config, "providers.openrouter.api_key", "")),
            "gemini": _mask_secret(safe_get(_config, "providers.gemini.api_key", "")),
            "groq": _mask_secret(safe_get(_config, "providers.groq.api_key", "")),
            "openai": _mask_secret(safe_get(_config, "providers.openai.api_key", "")),
            "anthropic": _mask_secret(safe_get(_config, "providers.anthropic.api_key", "")),
            "deepseek": _mask_secret(safe_get(_config, "providers.deepseek.api_key", "")),
            "custom": _mask_secret(safe_get(_config, "providers.custom.api_key", "")),
            "omniroute": _mask_secret(safe_get(_config, "providers.omniroute.api_key", "")),
        },
        "providerBases": {
            "custom": _mask_sensitive_url(safe_get(_config, "providers.custom.api_base", "")),
            "omniroute": _mask_sensitive_url(safe_get(_config, "providers.omniroute.api_base", "")),
        },
        "providerEnabled": {
            "openrouter": safe_get(_config, "providers.openrouter.enabled", True),
            "gemini": safe_get(_config, "providers.gemini.enabled", True),
            "groq": safe_get(_config, "providers.groq.enabled", True),
            "openai": safe_get(_config, "providers.openai.enabled", True),
            "anthropic": safe_get(_config, "providers.anthropic.enabled", True),
            "deepseek": safe_get(_config, "providers.deepseek.enabled", True),
            "custom": safe_get(_config, "providers.custom.enabled", True),
            "omniroute": safe_get(_config, "providers.omniroute.enabled", True),
        },
        "omnirouteCombo": safe_get(_config, "providers.omniroute.extra_headers", {}).get("X-Combo-Id", "") if safe_get(_config, "providers.omniroute.extra_headers") else "",
        "searchEngineId": _mask_secret(safe_get(_config, "tools.web.search.api_key", "")),
        "searchProvider": safe_get(_config, "tools.web.search.provider", "brave"),
        "braveKey": _mask_secret(safe_get(_config, "tools.web.search.api_key", "")),
        "tavilyKey": _mask_secret(safe_get(_config, "tools.web.search.tavily_key", "")),
    }




@app.post("/api/settings")
async def update_settings(data: SettingsUpdate):
    """Update agent configuration and save to disk."""
    global _config
    if not _config:
        raise HTTPException(status_code=500, detail="Config not loaded")
    
    # OpenCode secrets are server-only. Model/provider changes require restart:
    # this endpoint cannot safely swap an active provider in a running agent.
    if (data.providerKeys and "opencode" in data.providerKeys) or (data.providerBases and "opencode" in data.providerBases):
        raise HTTPException(status_code=400, detail="OpenCode: chave apenas no ambiente do servidor e endpoint fixo.")
    current_model = _config.agents.defaults.model
    if data.model is not None and data.model != current_model and (data.model.startswith("opencode/") or current_model.startswith("opencode/")):
        raise HTTPException(status_code=409, detail="Para alterar o modelo OpenCode, configure no servidor e reinicie o agente.")
    if data.providerEnabled and "opencode" in data.providerEnabled:
        raise HTTPException(status_code=409, detail="Configure OpenCode no servidor e reinicie o agente.")

    orig_telegram = _config.channels.telegram.enabled
    orig_email = _config.channels.email.enabled
    orig_whatsapp = _config.channels.evolution.enabled

    # AI Model Settings
    if data.model is not None: _config.agents.defaults.model = data.model
    if data.maxTokens is not None: _config.agents.defaults.max_tokens = data.maxTokens
    if data.temperature is not None: _config.agents.defaults.temperature = data.temperature
    if data.autonomous is not None: _config.agents.defaults.autonomous = data.autonomous
    if data.botName is not None and hasattr(_config, "bot_name"): 
        _config.bot_name = data.botName
    
    # Channels
    if data.telegramEnabled is not None: _config.channels.telegram.enabled = data.telegramEnabled
    if data.emailEnabled is not None: _config.channels.email.enabled = data.emailEnabled
    if data.whatsappEnabled is not None: _config.channels.evolution.enabled = data.whatsappEnabled

    # Channel Details
    if _new_secret(data.telegramToken): _config.channels.telegram.token = data.telegramToken
    if data.telegramAllowList is not None:
        _config.channels.telegram.allow_from = [x.strip() for x in data.telegramAllowList.split(",") if x.strip()]
    if data.telegramNotifyChatId is not None:
        _config.channels.telegram.notify_chat_id = data.telegramNotifyChatId
    
    if data.emailUser is not None:
        _config.channels.email.imap_username = data.emailUser
        _config.channels.email.smtp_username = data.emailUser
        _config.channels.email.from_address = data.emailUser
    if _new_secret(data.emailPass):
        _config.channels.email.imap_password = data.emailPass
        _config.channels.email.smtp_password = data.emailPass
    if data.smtpHost is not None: _config.channels.email.smtp_host = data.smtpHost
    if data.smtpPort is not None: _config.channels.email.smtp_port = data.smtpPort

    if data.evolutionBaseUrl is not None and data.evolutionBaseUrl != _SECRET_MASK: _config.channels.evolution.base_url = data.evolutionBaseUrl
    if _new_secret(data.evolutionApiKey): _config.channels.evolution.api_key = data.evolutionApiKey
    if data.evolutionInstance is not None: _config.channels.evolution.instance_name = data.evolutionInstance

    # Provider Settings
    if data.providerKeys:
        for p, key in data.providerKeys.items():
            if hasattr(_config.providers, p) and _new_secret(key):
                getattr(_config.providers, p).api_key = key

    if data.providerBases:
        for p, base in data.providerBases.items():
            if hasattr(_config.providers, p) and base != _SECRET_MASK:
                getattr(_config.providers, p).api_base = base if base else None

    if data.providerEnabled:
        for p, enabled in data.providerEnabled.items():
            if hasattr(_config.providers, p):
                getattr(_config.providers, p).enabled = enabled

    if data.omnirouteCombo is not None:
        if not _config.providers.omniroute.extra_headers:
            _config.providers.omniroute.extra_headers = {}
        if data.omnirouteCombo.strip():
            _config.providers.omniroute.extra_headers["X-Combo-Id"] = data.omnirouteCombo.strip()
        else:
            _config.providers.omniroute.extra_headers.pop("X-Combo-Id", None)

    # Search Tools
    if data.searchProvider is not None: _config.tools.web.search.provider = data.searchProvider
    if _new_secret(data.braveKey): _config.tools.web.search.api_key = data.braveKey
    if _new_secret(data.tavilyKey): _config.tools.web.search.tavily_key = data.tavilyKey

    # Persist to config.json
    from caiocore.config.loader import save_config
    save_config(_config)
    
    # Hot-reload in-memory agent settings
    if _agent:
        _agent.model = _config.agents.defaults.model
        _agent.max_tokens = _config.agents.defaults.max_tokens
        _agent.temperature = _config.agents.defaults.temperature

        # Hot-reload provider API key (critical: without this, new keys only work after restart)
        if hasattr(_agent, 'provider') and _agent.provider:
            # Match current model to a provider
            prov_cfg, pname = _config._match_provider(_agent.model)
            
            if prov_cfg and prov_cfg.api_key:
                _agent.provider.api_key = prov_cfg.api_key
                _agent.provider.api_base = _config.get_api_base(_agent.model)
                _agent.provider.extra_headers = prov_cfg.extra_headers or {}
                # Update gateway spec dynamically
                from caiocore.providers.registry import find_gateway
                _agent.provider._gateway = find_gateway(pname, prov_cfg.api_key, _agent.provider.api_base)
                # Also update env vars for litellm
                _agent.provider._setup_env(
                    prov_cfg.api_key,
                    _agent.provider.api_base,
                    _agent.model
                )
                logger.info(f"Settings: Hot-reloaded provider API key, headers and gateway spec for {pname}")

        # Hot-reload search tool settings
        # The tool is registered as "web_search" (from WebSearchTool.name)
        web_search = _agent.tools.get("web_search")
        if web_search:
            web_search.provider = _config.tools.web.search.provider
            web_search.brave_key = _config.tools.web.search.api_key
            web_search.tavily_key = _config.tools.web.search.tavily_key
            logger.info(f"Settings: Hot-reloaded web_search tool (Provider: {web_search.provider})")
        else:
            logger.warning("Settings: web_search tool not found in agent for hot-reload")

        logger.info("Settings: Hot-reloaded AI parameters to AgentLoop")

    reboot_needed = (
        orig_telegram != _config.channels.telegram.enabled or
        orig_email != _config.channels.email.enabled or
        orig_whatsapp != _config.channels.evolution.enabled
    )
    
    if reboot_needed:
        logger.warning("Settings: Channel configuration changed. Syncing ChannelManager...")
        if _channels:
            asyncio.create_task(_channels.sync_with_config(_config))

    return {
        "status": "ok",
        "reboot_required": reboot_needed,
        "message": "Configurações salvas com sucesso." + (" Reinicie o sistema para aplicar mudanças nos canais." if reboot_needed else "")
    }

class GoogleCredsRequest(BaseModel):
    content: str

@app.post("/api/settings/credentials/google")
async def setup_google_credentials(req: GoogleCredsRequest):
    """Receives credentials.json content, saves it, and returns the Authorization URL."""
    try:
        conteudo_str = req.content
        if not conteudo_str.strip() or "client_id" not in conteudo_str:
            raise HTTPException(status_code=400, detail="Invalid credentials.json content")
            
        import json
        cred_data = json.loads(conteudo_str)
        
        from caiocore.config.loader import get_data_dir
        # Fix: Use path relative to project root instead of get_data_dir().parent.parent which points to C:\Users
        caio_stack_core = _Path.cwd() / "caio-stack" / "core"
        caio_stack_core.mkdir(parents=True, exist_ok=True)
        
        cred_path = caio_stack_core / "credentials.json"
        cred_path.write_text(conteudo_str, encoding="utf-8")
        
        from google_auth_oauthlib.flow import Flow
        redirect_uri = cred_data.get('installed', cred_data.get('web', {})).get('redirect_uris', ['http://localhost'])[0]
        
        # Normalize localhost redirect_uri
        if "localhost" in redirect_uri and not redirect_uri.endswith("/"):
            redirect_uri += "/"
            
        flow = Flow.from_client_secrets_file(
            str(cred_path), 
            scopes=["https://www.googleapis.com/auth/calendar"],
            redirect_uri=redirect_uri
        )
        auth_url, state = flow.authorization_url(prompt='consent', access_type='offline')
        
        # Save state temporarily to be used in the confirmation step
        state_path = caio_stack_core / "oauth_state.json"
        state_path.write_text(json.dumps({"state": state}), encoding="utf-8")
        
        return {"status": "ok", "auth_url": auth_url, "message": "Credencial válida. Autorize no Google."}
    except HTTPException:
        raise
    except Exception:
        # OAuth exceptions may contain client secrets or authorization codes.
        logger.error("Google credential setup failed")
        raise HTTPException(status_code=500, detail="Google credential setup failed") from None

class GoogleConfirmRequest(BaseModel):
    response_url: str

@app.post("/api/settings/credentials/google/confirm")
async def confirm_google_oauth(req: GoogleConfirmRequest):
    """Exchanges the authorization_response URL for a token and enables Calendar."""
    try:
        from caiocore.config.loader import get_data_dir
        # Fix: Use path relative to project root instead of get_data_dir().parent.parent which points to C:\Users
        caio_stack_core = _Path.cwd() / "caio-stack" / "core"
        cred_path = caio_stack_core / "credentials.json"
        state_path = caio_stack_core / "oauth_state.json"
        
        if not cred_path.exists():
            raise HTTPException(status_code=400, detail="credentials.json not found")
            
        import json
        cred_data = json.loads(cred_path.read_text(encoding="utf-8"))
        redirect_uri = cred_data.get('installed', cred_data.get('web', {})).get('redirect_uris', ['http://localhost'])[0]
        
        # Normalize localhost redirect_uri
        if "localhost" in redirect_uri and not redirect_uri.endswith("/"):
            redirect_uri += "/"
            
        state = None
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8")).get("state")
            
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(cred_path), 
            scopes=["https://www.googleapis.com/auth/calendar"],
            state=state,
            redirect_uri=redirect_uri
        )
        
        import os
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        flow.fetch_token(authorization_response=req.response_url.strip())
        
        token_path = caio_stack_core / "token.json"
        token_path.write_text(flow.credentials.to_json(), encoding="utf-8")
        
        if _config:
            from caiocore.config.loader import save_config
            _config.tools.google_calendar.enabled = True
            _config.tools.google_calendar.credentials_path = str(cred_path)
            _config.tools.google_calendar.token_path = str(token_path)
            save_config(_config)
            
        return {"status": "success", "message": "Google Calendar configurado com sucesso!"}
    except HTTPException:
        raise
    except Exception as e:
        # OAuth exception messages and tracebacks can contain secrets.
        logger.error("Google OAuth confirmation failed")
        if type(e).__name__ == "MismatchingStateError":
            raise HTTPException(status_code=400, detail="Estado OAuth inválido; gere um novo link de autorização.") from None
        raise HTTPException(status_code=500, detail="Google OAuth confirmation failed") from None

class GoogleTokenDirectRequest(BaseModel):
    token_content: str

@app.post("/api/settings/credentials/google/token_direct")
async def save_google_token_direct(req: GoogleTokenDirectRequest):
    """Saves a directly provided token.json and enables Calendar."""
    try:
        from caiocore.config.loader import get_data_dir, save_config
        import json
        # Fix: Use path relative to project root instead of get_data_dir().parent.parent which points to C:\Users
        caio_stack_core = _Path.cwd() / "caio-stack" / "core"
        caio_stack_core.mkdir(parents=True, exist_ok=True)
        
        token_data = json.loads(req.token_content)
        if "token" not in token_data:
            raise ValueError("O JSON fornecido não se parece com um token do Google válido (chave 'token' ausente).")
            
        token_path = caio_stack_core / "token.json"
        token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
        
        # O credentials.json é opcional se o token.json tiver tudo, mas ativamos a tool mesmo assim
        if _config:
            _config.tools.google_calendar.enabled = True
            _config.tools.google_calendar.token_path = str(token_path)
            # Tenta referenciar credentials.json no mesmo lugar, se existir
            cred_path = caio_stack_core / "credentials.json"
            if cred_path.exists():
                _config.tools.google_calendar.credentials_path = str(cred_path)
            save_config(_config)
            
        return {"status": "success", "message": "Token do Google ativado com sucesso!"}
    except HTTPException:
        raise
    except Exception:
        logger.error("Google token setup failed")
        raise HTTPException(status_code=400, detail="Google token setup failed") from None




# ── Channel Webhooks ─────────────────────────────────────────────────

@app.post("/api/webhooks/evolution")
async def evolution_webhook(data: dict[str, Any]):
    """Receives WhatsApp events from Evolution API."""
    if not _channels:
        return {"status": "channels_not_ready"}
    
    # Forward to the evolution channel instance
    evo = _channels.get_channel("evolution")
    if not evo:
        return {"status": "evolution_channel_not_enabled"}
    
    # Process in background task to return 200 OK to Evolution ASAP
    asyncio.create_task(evo.handle_webhook(data))
    return {"status": "ok"}



def start_api(agent, bus, config, cron, channels=None, doc_agent=None):
    """
    Initialize the FastAPI server globals and dependencies.
    DOES NOT start the server itself — use start_api_server() for that.
    """
    global _agent, _bus, _config, _cron, _channels, _doc_agent
    _agent = agent
    _bus = bus
    _config = config
    _cron = cron
    _channels = channels
    _doc_agent = doc_agent

    # Initialize monitoring agents (synchronously setup registry)
    _init_monitoring_agents(config)
    
    logger.info("Caio API initialized (monitoring agents registered)")


async def start_api_server(host="127.0.0.1", port=18790):
    """
    RUN current FastAPI app in the current event loop.
    This prevents loop conflict errors (504/hangs).
    """
    import uvicorn
    global _local_queue_server_loopback
    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False, proxy_headers=False)
    server = uvicorn.Server(config)
    logger.info("Caio API Server starting on port {}...", port)
    _local_queue_server_loopback = host in {"127.0.0.1", "::1"}
    try:
        await server.serve()
    finally:
        _local_queue_server_loopback = False
