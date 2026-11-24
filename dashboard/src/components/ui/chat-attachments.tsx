import { useEffect, useRef, useState } from 'react';
import { Plus, Image, Camera, FileText, X } from 'lucide-react';
import { rasterType, validateAttachments } from './attachment-validation.js';
import './chat-attachments.css';

type Props = { files: File[]; onChange: (files: File[]) => void; disabled: boolean };

export function AttachmentPicker({ files, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const gallery = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const document = useRef<HTMLInputElement>(null);
  const plus = useRef<HTMLButtonElement>(null);

  function choose(input: HTMLInputElement | null) {
    setOpen(false);
    input?.click();
  }
  function selected(event: React.ChangeEvent<HTMLInputElement>) {
    const incoming = Array.from(event.currentTarget.files || []);
    event.currentTarget.value = ''; // allow the same file to be selected again
    if (!incoming.length) return;
    const problem = validateAttachments(files, incoming);
    setError(problem || '');
    if (!problem) onChange([...files, ...incoming]);
  }
  return <div className="attachment-picker" onKeyDown={event => {
    if (event.key === 'Escape' && open) { event.preventDefault(); setOpen(false); plus.current?.focus(); }
  }}>
    <button ref={plus} className="attachment-toggle" type="button" disabled={disabled} aria-label="Adicionar anexo" aria-expanded={open} aria-controls="attachment-options" onClick={() => setOpen(!open)}><Plus size={21} aria-hidden="true" /></button>
    {open && !disabled && <div id="attachment-options" className="attachment-menu" role="group" aria-label="Opções de anexo">
      <button type="button" onClick={() => choose(gallery.current)}><Image size={18} aria-hidden="true" /> Fotos da galeria</button>
      <button type="button" onClick={() => choose(camera.current)}><Camera size={18} aria-hidden="true" /> Câmera</button>
      <button type="button" onClick={() => choose(document.current)}><FileText size={18} aria-hidden="true" /> Arquivo</button>
    </div>}
    <input className="sr-only" tabIndex={-1} type="file" accept="image/*" multiple ref={gallery} onChange={selected} aria-label="Selecionar fotos da galeria" />
    <input className="sr-only" tabIndex={-1} type="file" accept="image/*" capture="environment" ref={camera} onChange={selected} aria-label="Tirar foto" />
    <input className="sr-only" tabIndex={-1} type="file" multiple ref={document} onChange={selected} aria-label="Selecionar arquivo" />
    <span className="sr-only" role="alert">{error}</span>
    {error && <span className="attachment-error" aria-hidden="true">{error}</span>}
  </div>;
}

export function AttachmentPreview({ files, onChange }: Omit<Props, 'disabled'>) {
  const [previews, setPreviews] = useState<({ file: File; url: string } | null)[]>([]);
  useEffect(() => {
    let active = true;
    const urls: string[] = [];
    setPreviews([]);
    Promise.all(files.map(async file => {
      try {
        const type = await rasterType(file);
        if (!active || !type) return null;
        // Force the verified raster MIME; do not expose the file to the network.
        const url = URL.createObjectURL(new Blob([file], { type }));
        urls.push(url);
        return { file, url };
      } catch { return null; }
    })).then(result => { if (active) setPreviews(result); });
    return () => { active = false; urls.forEach(url => URL.revokeObjectURL(url)); };
  }, [files]);
  if (!files.length) return null;
  return <section className="attachment-preview" aria-label="Anexos no rascunho">
    <p role="status">Anexos ainda não podem ser enviados ou analisados. Remova os arquivos para enviar a mensagem. Nada foi enviado.</p>
    <ul>{files.map((file, index) => <li key={index}>
      {previews[index]?.file === file ? <img src={previews[index]!.url} alt="" /> : <FileText size={22} aria-hidden="true" />}
      <span className="attachment-name">{file.name}<small>{(file.size / 1024 / 1024).toFixed(2)} MB</small></span>
      <button type="button" aria-label={`Remover ${file.name}`} onClick={() => onChange(files.filter((_, i) => i !== index))}><X size={18} aria-hidden="true" /></button>
    </li>)}</ul>
    <small>Até 5 arquivos, 10 MB cada e 20 MB no total. Prévia local; arquivos não são salvos.</small>
  </section>;
}
