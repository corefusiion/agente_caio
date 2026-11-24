import { useEffect, useRef, useCallback, useState } from 'react';
import { ArrowUp } from 'lucide-react';
import { CaioAvatar } from './caio-avatar';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api } from '../../services/api';
import { type TraceStep } from './agent-plan';
import ActivitySteps from './activity-steps';
import { MessageActions } from './message-actions';
import { ReplySuggestions } from './reply-suggestions';
import { ShareDraft } from './share-draft';
import { createWaitTracker, formatElapsed } from '../../lib/request-wait.js';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  steps?: TraceStep[];
  failed?: boolean;
}

type ShareProps = { sharedText: string | null; onSharedTextTaken: () => void; shareError: boolean; onDismissShareError: () => void };
export function AnimatedAIChat({ sessionId, onChatUpdate, ...share }: { sessionId: string; onChatUpdate?: (id: string, msg: string) => void } & ShareProps) {
  // A new tree prevents rendering private content from the previous session.
  return <SessionChat key={sessionId} sessionId={sessionId} onChatUpdate={onChatUpdate} {...share} />;
}

function SessionChat({ sessionId, onChatUpdate, sharedText, onSharedTextTaken, shareError, onDismissShareError }: { sessionId: string; onChatUpdate?: (id: string, msg: string) => void } & ShareProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [value, setValue] = useState('');
  const [receivedShare, setReceivedShare] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [wait, setWait] = useState<{seconds: number; notice: string} | null>(null);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const pendingRef = useRef<symbol | null>(null);
  const stopWaitRef = useRef<null | (() => void)>(null);
  const mountedRef = useRef(true);
  const currentSessionRef = useRef(sessionId);
  currentSessionRef.current = sessionId;
  const historyPristine = useRef(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const shouldScroll = useRef(true);

  useEffect(() => {
    let active = true;
    mountedRef.current = true;
    historyPristine.current = true;
    setMessages([]);
    setValue('');
    setSteps([]);
    setIsTyping(false);
    setWait(null);
    api.getChatHistory(sessionId).then((history: unknown) => {
      if (active && currentSessionRef.current === sessionId && historyPristine.current && Array.isArray(history)) {
        // History is not a source of trusted live trace steps.
        setMessages(history.filter((m: any) => (m?.role === 'user' || m?.role === 'assistant') && typeof m.content === 'string')
          .map((m: any) => ({ role: m.role, content: m.content })));
      }
    }).catch(() => { /* History can be unavailable; do not invent messages. */ });
    // No request ID in the shared stream: same-session events may belong to
    // another tab/request. Do not subscribe or attribute them to this reply.
    return () => { active = false; mountedRef.current = false; pendingRef.current = null; stopWaitRef.current?.(); stopWaitRef.current = null; };
  }, [sessionId]);

  useEffect(() => {
    if (shouldScroll.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, isTyping, steps]);

  const adjust = useCallback((reset = false) => {
    if (!textareaRef.current) return;
    textareaRef.current.style.height = 'auto';
    if (!reset) textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`;
  }, []);

  useEffect(() => { adjust(); }, [value, adjust]);

  const selectSuggestion = useCallback((suggestion: string) => {
    if (pendingRef.current || currentSessionRef.current !== sessionId) return;
    // Keep any draft; choosing a suggestion only edits the composer, never sends.
    setValue(previous => previous.length ? `${previous}\n${suggestion}` : suggestion);
    textareaRef.current?.focus();
  }, [sessionId]);

  const send = useCallback(async () => {
    if (!value.trim() || pendingRef.current) return;
    const msg = value.trim();
    const request = Symbol('chat request');
    const isCurrent = () => mountedRef.current && currentSessionRef.current === sessionId && pendingRef.current === request;
    const firstUserMsg = messages.find(m => m.role === 'user')?.content || msg;
    historyPristine.current = false;
    pendingRef.current = request;
    setSteps([]);
    setValue(''); setReceivedShare(false); adjust(true);
    shouldScroll.current = true;
    setMessages(prev => [...prev, { role: 'user', content: msg }]);
    setIsTyping(true);
    let stopWait: (() => void) | null = null;
    try {
      try { onChatUpdate?.(sessionId, firstUserMsg); } catch { /* Continue the chat request. */ }
      const response = api.sendChatMessage(msg, sessionId, null);
      stopWait = createWaitTracker((snapshot: { seconds: number; notice: string }) => {
        if (isCurrent()) setWait(snapshot);
      });
      stopWaitRef.current = stopWait;
      const res = await response;
      if (!isCurrent()) return;
      const success = !['error', 'timeout', 'initializing'].includes(res?.status) && typeof res?.content === 'string' && !!res.content.trim();
      const content = success
        ? res.content
        : res?.status === 'initializing' && typeof res.content === 'string'
          ? res.content
          : 'Não foi possível obter uma resposta. O trabalho pode continuar no servidor; verifique antes de tentar novamente.';
      setMessages(prev => [...prev, { role: 'assistant', content, failed: !success }]);
    } catch {
      if (isCurrent()) {
        setMessages(prev => [...prev, { role: 'assistant', content: 'Não foi possível obter uma resposta. O trabalho pode continuar no servidor; verifique antes de tentar novamente.', failed: true }]);
      }
    } finally {
      stopWait?.();
      if (stopWaitRef.current === stopWait) stopWaitRef.current = null;
      if (isCurrent()) { pendingRef.current = null; setIsTyping(false); setWait(null); setSteps([]); }
    }
  }, [value, messages, sessionId, onChatUpdate, adjust]);

  return (
    <div className="chat-shell">
      <header className="chat-header"><div className="chat-header-identity"><CaioAvatar /><div><span className="chat-eyebrow">ASSISTENTE PESSOAL</span><h1>Caio</h1></div></div><span className="chat-header-note">Sua conversa, do seu jeito</span></header>
      <div className="chat-scroll custom-scrollbar" ref={scrollRef} onScroll={e => { const el = e.currentTarget; shouldScroll.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; }}>
        <div className="chat-column">
          {messages.length === 0 && !isTyping && (
            <div className="chat-welcome"><CaioAvatar size="welcome" /><p className="chat-eyebrow">OLÁ, EU SOU O CAIO</p><h2>Como posso ajudar?</h2><p>Converse comigo, organize ideias e acompanhe as atividades da sua conversa.</p></div>
          )}
          {messages.map((m, i) => (
            <article className={`chat-message ${m.role === 'user' ? 'chat-message-user' : 'chat-message-assistant'}`} key={i}>
              <div className="chat-message-label">{m.role === 'user' ? 'Você' : 'Caio'}</div>
              {m.steps && m.steps.length > 0 && <details className="trace-details"><summary>Ver atividade desta resposta</summary><ActivitySteps steps={m.steps} pending={false} connected={false} /></details>}
              <div className="chat-message-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                  a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
                  // Do not fetch remote images from untrusted assistant content.
                  img: ({ alt }) => <span>[Imagem: {alt || 'sem descrição'}]</span>,
                }}>{m.content}</ReactMarkdown>
              </div>
              {m.role === 'assistant' && !m.failed && <MessageActions content={m.content} />}
              {m.role === 'assistant' && !m.failed && i === messages.length - 1 && !isTyping && <ReplySuggestions content={m.content} onSelect={selectSuggestion} />}
            </article>
          ))}
          {isTyping && wait && <div className="chat-activity"><div className="chat-message-label">Caio</div>
            <section className="request-wait" aria-label="Espera desta resposta">
              <p role="status" aria-live="polite" aria-atomic="true">{wait.notice}</p>
              <p aria-live="off">Tempo aguardando: {formatElapsed(wait.seconds)}</p>
            </section>
          </div>}
        </div>
      </div>
      <div className="chat-compose-area"><form className="chat-compose-column" onSubmit={e => { e.preventDefault(); send(); }}>
        {receivedShare && <p role="status">Texto compartilhado adicionado ao rascunho. Revise antes de enviar. Links não foram abertos.</p>}
        {shareError && <p role="alert">Não foi possível receber o compartilhamento, ou ele expirou. Copie e cole o texto. <button type="button" onClick={onDismissShareError}>Fechar</button></p>}
        {sharedText !== null && <section className="share-draft-panel" aria-label="Prévia do compartilhamento">
          <p>Prévia do compartilhamento. Nenhum link foi aberto e nada foi enviado.</p>
          <label htmlFor="received-preview">Texto recebido</label>
          <textarea id="received-preview" rows={4} readOnly value={sharedText} />
          <p>Confira a conversa antes de adicionar. Não compartilhe informações sensíveis.</p>
          <button type="button" disabled={isTyping} onClick={() => {
            if (value && !window.confirm('Já existe texto no rascunho. Acrescentar o compartilhamento ao final?')) return;
            setValue(previous => previous ? `${previous}\n${sharedText}` : sharedText);
            setReceivedShare(true); onSharedTextTaken();
          }}>Adicionar compartilhamento ao rascunho</button>
          <button type="button" onClick={onSharedTextTaken}>Descartar compartilhamento</button>
        </section>}
        <ShareDraft disabled={isTyping} onPrepare={selectSuggestion} />
        <div className="chat-composer"><label className="sr-only" htmlFor="chat-input">Mensagem para o Caio</label><textarea id="chat-input" ref={textareaRef} rows={1} placeholder="Escreva sua mensagem..." value={value} onChange={e => { setValue(e.target.value); adjust(); }} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} /><button type="submit" disabled={!value.trim() || isTyping} aria-label="Enviar mensagem"><ArrowUp size={20} aria-hidden="true" /></button></div>
        <p className="chat-hint">Enter para enviar · Shift + Enter para nova linha</p>
      </form></div>
    </div>
  );
}
