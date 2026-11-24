// Client-side limits protect the composer only; a future server must enforce its own.
export const MAX_FILES = 5;
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 20 * 1024 * 1024;

export function validateAttachments(current, incoming) {
  if (!incoming.length) return null;
  if (current.length + incoming.length > MAX_FILES) return 'Escolha no máximo 5 arquivos.';
  if (incoming.some(file => !file.size)) return 'Arquivos vazios não podem ser adicionados.';
  if (incoming.some(file => file.size > MAX_FILE_BYTES)) return 'Cada arquivo deve ter no máximo 10 MB.';
  if ([...current, ...incoming].reduce((total, file) => total + file.size, 0) > MAX_TOTAL_BYTES) return 'O total dos arquivos deve ser de no máximo 20 MB.';
  return null;
}

// Only raster formats with a matching signature may be handed to an <img>.
// SVG, HTML, PDFs and other files are shown as names only, never embedded.
export async function rasterType(file) {
  const b = new Uint8Array(await file.slice(0, 12).arrayBuffer());
  if (file.type === 'image/png' && b.length >= 8 && [137,80,78,71,13,10,26,10].every((v, i) => b[i] === v)) return 'image/png';
  if (file.type === 'image/jpeg' && b.length >= 3 && b[0] === 255 && b[1] === 216 && b[2] === 255) return 'image/jpeg';
  if (file.type === 'image/gif' && b.length >= 6 && ['GIF87a', 'GIF89a'].includes(String.fromCharCode(...b.slice(0, 6)))) return 'image/gif';
  if (file.type === 'image/webp' && b.length >= 12 && String.fromCharCode(...b.slice(0, 4)) === 'RIFF' && String.fromCharCode(...b.slice(8, 12)) === 'WEBP') return 'image/webp';
  return null;
}
