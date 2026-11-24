import { useEffect, useRef, useState } from 'react';
import { Check, Copy, Square, ThumbsDown, ThumbsUp, Volume2 } from 'lucide-react';
import './message-actions.css';

export type MessageActionsProps = {
  content: string;
};

type Reaction = 'like' | 'dislike' | null;

// SpeechSynthesis is shared by the whole page. Only cancel speech started here.
let activeSpeech: SpeechSynthesisUtterance | null = null;

/** Reactions stay local; this component makes no network requests. */
export function MessageActions({ content }: MessageActionsProps) {
  // Remount when a recycled message component receives different content, so its
  // reaction, pending clipboard feedback and speech never transfer to another reply.
  return <MessageActionsForContent key={content} content={content} />;
}

function MessageActionsForContent({ content }: MessageActionsProps) {
  const [reaction, setReaction] = useState<Reaction>(null);
  const [speaking, setSpeaking] = useState(false);
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState('');
  const mounted = useRef(false);
  const speech = useRef<SpeechSynthesisUtterance | null>(null);
  const copyRequest = useRef(0);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      copyRequest.current += 1;
      if (copyTimer.current) clearTimeout(copyTimer.current);
      if (speech.current && activeSpeech === speech.current) {
        activeSpeech = null;
        speech.current = null;
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  async function copy() {
    const request = ++copyRequest.current;
    if (copyTimer.current) clearTimeout(copyTimer.current);
    setCopied(false);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(content);
      if (!mounted.current || request !== copyRequest.current) return;
      setCopied(true);
      setStatus('Resposta copiada.');
      copyTimer.current = setTimeout(() => {
        if (mounted.current && request === copyRequest.current) setCopied(false);
      }, 2000);
    } catch {
      if (!mounted.current || request !== copyRequest.current) return;
      setStatus('Não foi possível copiar. Verifique a permissão da área de transferência.');
    }
  }

  function react(next: Exclude<Reaction, null>) {
    const updated = reaction === next ? null : next;
    setReaction(updated);
    setStatus(updated ? 'Reação temporária, somente neste dispositivo; não é enviada.' : 'Reação removida.');
  }

  function stop() {
    const utterance = speech.current;
    if (utterance && activeSpeech === utterance) {
      activeSpeech = null;
      speech.current = null;
      window.speechSynthesis.cancel();
    }
    setSpeaking(false);
    setStatus('Leitura interrompida.');
  }

  function listen() {
    if (typeof window === 'undefined' || !('speechSynthesis' in window) ||
        typeof window.SpeechSynthesisUtterance !== 'function') {
      setStatus('Leitura em voz alta não é compatível com este navegador.');
      return;
    }
    const synth = window.speechSynthesis;
    // cancel() affects all speech on the page, so don't interrupt another owner.
    if (activeSpeech || synth.speaking || synth.pending) {
      setStatus('Outra leitura está em andamento. Aguarde para ouvir esta resposta.');
      return;
    }
    try {
      // Never let the browser choose a default voice; it might be remote.
      // Missing or unknown localService is treated as non-local.
      const localVoices = synth.getVoices().filter(voice => voice.localService === true);
      const voice = localVoices.find(v => /^pt-BR$/i.test(v.lang))
        ?? localVoices.find(v => /^pt(?:-|$)/i.test(v.lang))
        ?? localVoices[0];
      if (!voice) {
        setStatus('Nenhuma voz local disponível. A leitura não será enviada a serviços externos.');
        return;
      }
      const utterance = new window.SpeechSynthesisUtterance(content);
      utterance.voice = voice;
      utterance.lang = voice.lang;
      speech.current = utterance;
      activeSpeech = utterance;
      const finish = (message: string) => {
        if (activeSpeech !== utterance || speech.current !== utterance) return;
        activeSpeech = null;
        speech.current = null;
        if (mounted.current) {
          setSpeaking(false);
          setStatus(message);
        }
      };
      utterance.onend = () => finish('Leitura concluída.');
      utterance.onerror = () => finish('Não foi possível ler a resposta em voz alta.');
      setSpeaking(true);
      setStatus('Lendo resposta em voz alta.');
      synth.speak(utterance);
    } catch {
      if (activeSpeech === speech.current) activeSpeech = null;
      speech.current = null;
      setSpeaking(false);
      setStatus('Não foi possível ler a resposta em voz alta.');
    }
  }

  return (
    <div className="caio-message-actions">
      <div className="caio-message-actions__buttons" role="group" aria-label="Ações da resposta">
        <button type="button" onClick={copy} aria-label={copied ? 'Resposta copiada' : 'Copiar resposta'} title={copied ? 'Copiado' : 'Copiar resposta'}>
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        </button>
        <button type="button" onClick={() => react('like')} aria-label="Curtir resposta (reação local temporária)" aria-pressed={reaction === 'like'} title="Curtir (reação local temporária)">
          <ThumbsUp aria-hidden="true" />
        </button>
        <button type="button" onClick={() => react('dislike')} aria-label="Não curtir resposta (reação local temporária)" aria-pressed={reaction === 'dislike'} title="Não curtir (reação local temporária)">
          <ThumbsDown aria-hidden="true" />
        </button>
        {speaking ? (
          <button type="button" onClick={stop} aria-label="Parar leitura" title="Parar leitura"><Square aria-hidden="true" /></button>
        ) : (
          <button type="button" onClick={listen} aria-label="Ouvir resposta" title="Ouvir resposta"><Volume2 aria-hidden="true" /></button>
        )}
      </div>
      <span className="caio-message-actions__status" role="status" aria-live="polite" aria-atomic="true">{status}</span>
    </div>
  );
}
