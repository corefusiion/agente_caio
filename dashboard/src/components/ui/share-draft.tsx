import { useState } from 'react';

const MAX_TEXT = 8192;
const REQUEST = 'Quero um briefing e sugestões de tarefas com base apenas no texto que eu fornecer. Não abra links nem diga que assistiu ao vídeo. Se faltar informação, pergunte antes de concluir.';

/** Manual fallback when native sharing is unavailable. */
export function ShareDraft({ onPrepare, disabled }: { onPrepare: (text: string) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState('');
  const close = () => { setDraft(''); setNotice(''); setOpen(false); };
  return <section className="share-draft" aria-label="Compartilhar texto ou link">
    <button type="button" className="share-draft-toggle" aria-expanded={open} aria-controls="share-draft-panel" onClick={() => open ? close() : setOpen(true)}>Colar texto ou link</button>
    {open && <div id="share-draft-panel" className="share-draft-panel">
      <p>No Instagram, TikTok ou YouTube, copie o link; volte ao Caio e use Colar neste campo. O recebimento pelo menu Compartilhar do Android está desativado por segurança.</p>
      <p>Não cole senhas, códigos ou links com acesso privado. Revise antes de adicionar ao rascunho e decidir se quer enviar. Ao fechar ou trocar de conversa, a prévia é descartada.</p>
      <label htmlFor="share-draft-text">Texto ou link para revisar</label>
      <textarea id="share-draft-text" rows={3} maxLength={MAX_TEXT} value={draft} onChange={e => {
        const next = e.target.value;
        if (next.length > MAX_TEXT) { setNotice('Limite de 8.192 caracteres. Reduza o texto e tente novamente.'); return; }
        setDraft(next); setNotice('');
      }} onDrop={e => { e.preventDefault(); setNotice('Arquivos e itens arrastados não são aceitos. Copie e cole somente texto ou link.'); }} onPaste={e => {
        // Consume only the user-initiated paste event, never read the clipboard in the background.
        e.preventDefault();
        const data = e.clipboardData;
        if (!data) { setNotice('Não foi possível ler o texto colado. Tente novamente.'); return; }
        if (data.files.length || Array.from(data.items || []).some(item => item.kind === 'file')) {
          setNotice('Arquivos, inclusive vídeos, não são aceitos. Copie e cole somente texto ou link.'); return;
        }
        const text = data.getData('text/plain');
        if (!text) { setNotice('Nenhum texto simples encontrado. Copie um texto ou link e tente novamente.'); return; }
        const { selectionStart, selectionEnd } = e.currentTarget;
        const next = draft.slice(0, selectionStart) + text + draft.slice(selectionEnd);
        if (next.length > MAX_TEXT) { setNotice('Texto não colado: o limite é de 8.192 caracteres. Reduza e tente novamente.'); return; }
        setDraft(next); setNotice('Texto colado para revisão; ainda não foi enviado.');
      }} aria-describedby="share-draft-limits" />
      <p id="share-draft-limits">Até 8.192 caracteres. Arquivos de vídeo não são aceitos. Caio não analisa o vídeo por esse link; para conversar sobre ele, forneça uma transcrição ou descrição.</p>
      <p>Ao enviar pelo chat, o conteúdo pode ser registrado e enviado ao provedor de IA. Não inclua informações sensíveis.</p>
      <div className="share-draft-actions"><button type="button" disabled={disabled || !draft.trim()} onClick={() => { onPrepare(`${REQUEST}\n\nConteúdo fornecido:\n${draft.trim()}`); close(); }}>Adicionar ao rascunho do chat</button><button type="button" onClick={close}>Limpar e fechar</button></div>
      <p>O rascunho não é enviado automaticamente. Tarefas são apenas sugestões, não são criadas automaticamente.</p>
      <p role="status" aria-live="polite">{notice}</p>
    </div>}
  </section>;
}
