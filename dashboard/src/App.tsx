import React, { useState, useEffect, useCallback, useRef } from 'react';
import { 
  Settings, MessageSquare, Activity, Layers,
  PanelLeftClose, PanelLeftOpen, Plus, MoreHorizontal, 
  Pin, Trash2, Pencil, Check, X, Shield, Globe, 
  Mail, Calendar, Workflow, Sparkles
} from 'lucide-react';
import { motion, AnimatePresence, MotionConfig } from 'framer-motion';
import CoreSettings from './pages/CoreSettings';
import MonitorPage from './pages/MonitorPage';
import { AnimatedAIChat } from './components/ui/animated-ai-chat';
import AgentsPage from './pages/AgentsPage';
import ArtifactCanvas from './components/ui/artifact-canvas';
import { cn } from './lib/utils';
import { readMessageTextSize, writeMessageTextSize } from './lib/display-preference';
import { startShareReceipt } from './lib/share-receipt';
import './chat.css';

/* ── Chat session helpers ──────────────────────────────────────── */
interface ChatSession {
  id: string;
  title: string;
  lastMessage: string;
  updatedAt: number;
  pinned?: boolean;
  enabledSkills?: string[];
}

function loadSessions(): ChatSession[] {
  try { return JSON.parse(localStorage.getItem('caio_sessions') || '[]'); } catch { return []; }
}
function saveSessions(s: ChatSession[]) { localStorage.setItem('caio_sessions', JSON.stringify(s)); }
function createSessionId() { return `chat-${Date.now()}`; }

function updateSessionInList(sessions: ChatSession[], id: string, firstMsg: string): ChatSession[] {
  const title = firstMsg.slice(0, 40) + (firstMsg.length > 40 ? '...' : '');
  const exists = sessions.find(s => s.id === id);
  if (exists) {
    return sessions.map(s => s.id === id ? { ...s, lastMessage: firstMsg, updatedAt: Date.now() } : s);
  }
  return [{ id, title, lastMessage: firstMsg, updatedAt: Date.now(), enabledSkills: [] }, ...sessions].slice(0, 30);
}

/* ── App ───────────────────────────────────────────────────────── */
function App() {
  const [currentPage, setCurrentPage] = useState('chat');
  const [sharedText, setSharedText] = useState<string | null>(null);
  const [shareError, setShareError] = useState(false);
  const shareEpoch = useRef(0);
  useEffect(() => {
    let live = true;
    const epoch = shareEpoch.current;
    startShareReceipt().then(receipt => {
      if (!live || epoch !== shareEpoch.current || !receipt) return;
      if (receipt.text) { setSharedText(receipt.text); setCurrentPage('chat'); }
      else setShareError(true);
    });
    return () => { live = false; };
  }, []);
  const [messageTextSize, setMessageTextSize] = useState<'standard' | 'large'>(() => {
    try { return readMessageTextSize(window.localStorage); } catch { return 'standard'; }
  });
  const changeMessageTextSize = (value: 'standard' | 'large') => {
    setMessageTextSize(value);
    try { return writeMessageTextSize(window.localStorage, value); } catch { return false; }
  };
  const [isSidebarOpen, setIsSidebarOpen] = useState(() => !window.matchMedia('(max-width: 767px)').matches);
  const [activeArtifact, setActiveArtifact] = useState<{type: 'code' | 'markdown' | 'image', title: string, content: string} | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>(loadSessions);
  const [activeSessionId, setActiveSessionId] = useState(() => {
    const s = loadSessions();
    return s.length > 0 ? s[0].id : createSessionId();
  });
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [isSkillModalOpen, setIsSkillModalOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const skillDialogRef = useRef<HTMLDivElement>(null);
  const closeMobileSidebar = () => { if (window.matchMedia('(max-width: 767px)').matches) setIsSidebarOpen(false); };

  const navItems = [
    { id: 'chat',     label: 'Mensagens',       icon: MessageSquare },
    { id: 'monitor',  label: 'Sistema',         icon: Activity },
    { id: 'agents',   label: 'Habilidades',     icon: Layers },
    { id: 'settings', label: 'Preferências',    icon: Settings },
  ];

  const availableSkills = [
    { id: 'filesystem', label: 'Arquivos', icon: Shield },
    { id: 'search', label: 'Busca na web', icon: Globe },
    { id: 'email', label: 'E-mail', icon: Mail },
    { id: 'calendar', label: 'Calendário', icon: Calendar },
    { id: 'workflow', label: 'Automação', icon: Workflow },
    { id: 'mcp', label: 'Ferramentas externas', icon: Sparkles },
  ];

  useEffect(() => { saveSessions(sessions); }, [sessions]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpenId(null);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { setMenuOpenId(null); setIsSkillModalOpen(false); closeMobileSidebar(); } };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!isSkillModalOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = skillDialogRef.current;
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex="-1"])') || []);
    focusable()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', trap);
    return () => { document.removeEventListener('keydown', trap); previous?.focus(); };
  }, [isSkillModalOpen]);

  const onChatUpdate = useCallback((sessionId: string, firstMessage: string) => {
    setSessions(prev => updateSessionInList(prev, sessionId, firstMessage));
  }, []);

  const handleNewChat = () => {
    shareEpoch.current++;
    setSharedText(null);
    const id = createSessionId();
    setActiveSessionId(id);
    setCurrentPage('chat');
    closeMobileSidebar();
  };

  const handleSelectSession = (id: string) => {
    shareEpoch.current++;
    setSharedText(null);
    setActiveSessionId(id);
    setCurrentPage('chat');
    closeMobileSidebar();
  };

  const handleDeleteSession = (id: string) => {
    const updated = sessions.filter(s => s.id !== id);
    setSessions(updated);
    if (activeSessionId === id) {
      shareEpoch.current++;
      setSharedText(null);
      setActiveSessionId(updated.length > 0 ? updated[0].id : createSessionId());
    }
    setMenuOpenId(null);
  };

  const handleTogglePin = (id: string) => {
    setSessions(prev => prev.map(s => s.id === id ? { ...s, pinned: !s.pinned } : s));
    setMenuOpenId(null);
  };

  const startEditing = (s: ChatSession) => {
    setEditingSessionId(s.id);
    setEditTitle(s.title);
    setMenuOpenId(null);
  };

  const saveEdit = () => {
    if (editingSessionId && editTitle.trim()) {
      setSessions(prev => prev.map(s => s.id === editingSessionId ? { ...s, title: editTitle.trim() } : s));
    }
    setEditingSessionId(null);
  };

  const sortedSessions = [...sessions].sort((a, b) => {
    if (a.pinned && !b.pinned) return -1;
    if (!a.pinned && b.pinned) return 1;
    return b.updatedAt - a.updatedAt;
  });

  return (
    <MotionConfig reducedMotion="user"><div className={cn("flex h-screen bg-white text-zinc-900 overflow-hidden font-sans antialiased", messageTextSize === 'large' && 'large-message-text')} style={{ height: '100dvh' }}>
      
      {/* ── Arsenal Command Modal ───────────────────────── */}
      <AnimatePresence>
        {isSkillModalOpen && (
          <motion.div 
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-[200] flex items-center justify-center bg-zinc-900/40 backdrop-blur-sm p-4" role="dialog" aria-modal="true" aria-labelledby="skills-title"
          >
            <motion.div ref={skillDialogRef}
              initial={{ scale: 0.98, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.98, opacity: 0 }}
              className="bg-white w-full max-w-xl rounded-2xl shadow-2xl overflow-hidden border border-zinc-200"
            >
              <div className="px-6 py-5 border-b border-zinc-100 flex items-center justify-between">
                <div>
                  <h2 id="skills-title" className="text-lg font-bold text-zinc-900">Capacidades do Agente</h2>
                  <p className="text-xs text-zinc-500">Categorias ilustrativas. A ativação e o estado real das habilidades ainda não estão disponíveis nesta tela.</p>
                </div>
                <button aria-label="Fechar habilidades" onClick={() => setIsSkillModalOpen(false)} className="p-2 hover:bg-zinc-100 rounded-lg transition-colors"><X className="w-5 h-5 text-zinc-400" /></button>
              </div>
              <div className="p-6 grid grid-cols-1 gap-3 max-h-[50vh] overflow-y-auto custom-scrollbar">
                {availableSkills.map(skill => {
                  const Icon = skill.icon;
                  return (
                    <div key={skill.id} className="flex items-center gap-4 p-4 rounded-xl border border-zinc-200 text-left">
                      <div className="p-2 rounded-lg bg-zinc-100"><Icon aria-hidden="true" className="w-5 h-5" /></div>
                      <div className="flex-1">
                        <p className="text-sm font-bold">{skill.label}</p>
                        <p className="text-xs text-zinc-600 mt-0.5">Disponibilidade não verificada</p>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="px-6 py-4 bg-zinc-50 border-t border-zinc-100 flex justify-end">
                <button onClick={() => setIsSkillModalOpen(false)} className="px-5 py-2 bg-zinc-900 text-white rounded-lg font-bold text-xs hover:bg-zinc-800 transition-all">
                  Fechar
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Sidebar ──────────────────────────────────────── */}
      {isSidebarOpen && <button className="sidebar-scrim" aria-label="Fechar menu" onClick={() => setIsSidebarOpen(false)} />}
      <AnimatePresence initial={false}>
        {isSidebarOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 280, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="app-sidebar flex flex-col h-full bg-[#FAFAFA] overflow-hidden shrink-0 border-r border-zinc-200/60"
          >
            <div className="flex flex-col h-full w-[280px]">
              {/* Header Actions */}
              <div className="h-14 flex items-center justify-between px-4 shrink-0 mt-1">
                <button aria-label="Fechar menu" onClick={() => setIsSidebarOpen(false)} className="p-2 rounded-lg text-zinc-400 hover:text-zinc-900 hover:bg-white transition-all shadow-sm shadow-transparent hover:shadow-zinc-200/50">
                  <PanelLeftClose className="w-4 h-4" />
                </button>
                <div className="flex items-center gap-1">
                  <button onClick={() => setIsSkillModalOpen(true)} className="p-2 rounded-lg text-zinc-400 hover:text-zinc-900 hover:bg-white transition-all shadow-sm shadow-transparent hover:shadow-zinc-200/50" aria-label="Ver categorias de habilidades" title="Ver categorias de habilidades">
                    <Sparkles className="w-4 h-4" />
                  </button>
                  <button onClick={handleNewChat} className="p-2 rounded-lg bg-white border border-zinc-200 text-zinc-900 hover:border-zinc-900 transition-all shadow-sm active:scale-95" title="Novo Chat">
                    <Plus className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Primary Nav */}
              <nav className="px-3 space-y-0.5 mt-2 shrink-0">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = currentPage === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => { if (item.id !== 'chat') { shareEpoch.current++; setSharedText(null); } setCurrentPage(item.id); closeMobileSidebar(); }}
                      aria-current={isActive ? 'page' : undefined}
                      className={cn(
                        "w-full flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] transition-all",
                        isActive
                          ? "bg-white text-zinc-900 font-bold shadow-sm ring-1 ring-zinc-200"
                          : "text-zinc-500 hover:text-zinc-900 hover:bg-white/60 font-medium"
                      )}
                    >
                      <Icon className={cn("w-3.5 h-3.5", isActive ? "text-zinc-900" : "")} />
                      <span>{item.label}</span>
                    </button>
                  );
                })}
              </nav>

              {/* Chat Channels Section */}
              <div className="flex-1 overflow-y-auto custom-scrollbar mt-6 px-3 pb-6 relative">
                <div className="mb-2 px-2 flex items-center justify-between">
                  <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest">Recentes</span>
                </div>
                <div className="space-y-0.5">
                  {sortedSessions.map(s => {
                    const isActive = activeSessionId === s.id && currentPage === 'chat';
                    const isEditing = editingSessionId === s.id;

                    return (
                      <div 
                        key={s.id} 
                        className={cn(
                          "group relative flex items-center w-full rounded-lg text-[13px] transition-all px-1",
                          isActive
                            ? "bg-white text-zinc-900 font-bold shadow-sm ring-1 ring-zinc-200"
                            : "text-zinc-500 hover:text-zinc-900 hover:bg-white/80"
                        )}
                      >
                        {isEditing ? (
                          <div className="flex items-center gap-1 w-full px-1 py-1.5">
                            <input autoFocus className="bg-zinc-100 border-none rounded px-2 py-1 w-full text-xs font-medium outline-none focus:ring-1 focus:ring-zinc-300" value={editTitle} onChange={e => setEditTitle(e.target.value)} onKeyDown={e => { if(e.key==='Enter') saveEdit(); if(e.key==='Escape') setEditingSessionId(null); }} />
                            <button onClick={saveEdit} className="p-1 text-zinc-900"><Check className="w-3.5 h-3.5" /></button>
                          </div>
                        ) : (
                          <>
                            <button onClick={() => handleSelectSession(s.id)} className="flex-1 text-left px-2.5 py-2 truncate">
                              <span className="flex items-center gap-2">
                                {s.pinned ? <Pin className="w-3 h-3 text-zinc-900 fill-zinc-900" /> : <div className="w-1 h-1 rounded-full bg-zinc-300 group-hover:bg-zinc-400" />}
                                <span className="truncate">{s.title}</span>
                              </span>
                            </button>
                            
                            <div className={cn("session-menu-trigger flex items-center opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity px-1", menuOpenId === s.id ? 'opacity-100' : '')}>
                              <button aria-label={`Opções da conversa ${s.title}`} aria-expanded={menuOpenId === s.id} onClick={(e) => { e.stopPropagation(); setMenuOpenId(menuOpenId === s.id ? null : s.id); }} className="p-1 rounded-md hover:bg-zinc-100 text-zinc-400">
                                <MoreHorizontal className="w-3.5 h-3.5" />
                              </button>
                            </div>

                            {menuOpenId === s.id && (
                              <div ref={menuRef} className="absolute left-6 top-8 z-[100] w-40 bg-white rounded-xl shadow-2xl border border-zinc-200 py-1.5 overflow-hidden animate-in">
                                <button onClick={() => handleTogglePin(s.id)} className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[12px] text-zinc-600 hover:bg-zinc-50 transition-colors">
                                  <Pin className="w-3.5 h-3.5" /> {s.pinned ? 'Desafixar' : 'Fixar'}
                                </button>
                                <button onClick={() => startEditing(s)} className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[12px] text-zinc-600 hover:bg-zinc-50 transition-colors">
                                  <Pencil className="w-3.5 h-3.5" /> Renomear
                                </button>
                                <div className="h-px bg-zinc-100 my-1" />
                                <button onClick={() => handleDeleteSession(s.id)} className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[12px] font-bold text-red-500 hover:bg-red-50 transition-colors">
                                  <Trash2 className="w-3.5 h-3.5" /> Excluir
                                </button>
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Bottom Profile */}
              <div className="p-4 shrink-0 bg-white/40">
                <div className="flex items-center gap-3 px-2 py-2 rounded-xl hover:bg-white transition-all cursor-pointer group">
                  <div className="w-8 h-8 rounded-lg bg-zinc-900 flex items-center justify-center text-white text-[10px] font-black shadow-lg">
                    GS
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="text-[13px] text-zinc-900 font-bold truncate leading-none">Gleisson Santos</span>
                    <span className="text-[10px] text-zinc-400 mt-1 uppercase font-bold tracking-tighter">Caio Engine v5.0</span>
                  </div>
                </div>
              </div>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* ── Main Canvas ──────────────────────────────────── */}
      <main className="flex-1 flex flex-col h-full overflow-hidden min-w-0 relative bg-white">
        <AnimatePresence>
          {activeArtifact && (
            <ArtifactCanvas 
              type={activeArtifact.type} 
              title={activeArtifact.title} 
              content={activeArtifact.content} 
              onClose={() => setActiveArtifact(null)} 
            />
          )}
        </AnimatePresence>

        {/* Sidebar Toggle (Floating) */}
        {!isSidebarOpen && (
          <div className="absolute top-4 left-4 z-50">
            <button aria-label="Abrir menu" aria-expanded="false" onClick={() => setIsSidebarOpen(true)} className="p-2.5 rounded-lg bg-white text-zinc-400 hover:text-zinc-900 shadow-sm border border-zinc-200 transition-all">
              <PanelLeftOpen className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* Session changes remount the keyed chat immediately; do not retain its draft in an exit animation. */}
        <AnimatePresence mode="wait">
          <motion.div
            key={currentPage}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="flex-1 w-full h-full overflow-hidden flex flex-col"
          >
            {currentPage === 'chat' && (
              <AnimatedAIChat 
                key={activeSessionId}
                sessionId={activeSessionId} 
                onChatUpdate={onChatUpdate}
                sharedText={sharedText}
                onSharedTextTaken={() => setSharedText(null)}
                shareError={shareError}
                onDismissShareError={() => setShareError(false)}
              />
            )}
            {currentPage !== 'chat' && (
               <div className="app-page flex-1 overflow-y-auto custom-scrollbar p-6 md:p-12">
                  <div className="max-w-4xl mx-auto">
                    {currentPage === 'monitor' && <MonitorPage />}
                    {currentPage === 'agents' && <AgentsPage />}
                    {currentPage === 'settings' && <CoreSettings messageTextSize={messageTextSize} onMessageTextSizeChange={changeMessageTextSize} />}
                  </div>
               </div>
            )}
          </motion.div>
        </AnimatePresence>
      </main>
    </div></MotionConfig>
  );
}

export default App;
