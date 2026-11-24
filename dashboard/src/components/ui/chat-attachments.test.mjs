// Run: node --test src/components/ui/chat-attachments.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { validateAttachments, rasterType, MAX_FILE_BYTES, MAX_TOTAL_BYTES } from './attachment-validation.js';

const fake = (name, size, type = '') => ({ name, size, type });
test('limites são aplicados ao conjunto, sem aceitar arquivos vazios', () => {
  assert.equal(validateAttachments([], [fake('ok', MAX_FILE_BYTES)]), null);
  assert.match(validateAttachments([], [fake('vazio', 0)]), /vazios/);
  assert.match(validateAttachments([], [fake('grande', MAX_FILE_BYTES + 1)]), /10 MB/);
  assert.match(validateAttachments(Array(5).fill(fake('a', 1)), [fake('b', 1)]), /5 arquivos/);
  assert.match(validateAttachments([fake('a', MAX_FILE_BYTES), fake('b', MAX_FILE_BYTES)], [fake('c', 1)]), /20 MB/);
  assert.equal(MAX_TOTAL_BYTES, 20 * 1024 * 1024);
});
test('prévia aceita só assinaturas raster compatíveis; SVG e outros não são incorporados', async () => {
  const file = (bytes, type) => ({ type, slice: () => ({ arrayBuffer: async () => Uint8Array.from(bytes).buffer }) });
  assert.equal(await rasterType(file([137,80,78,71,13,10,26,10], 'image/png')), 'image/png');
  assert.equal(await rasterType(file([255,216,255], 'image/jpeg')), 'image/jpeg');
  assert.equal(await rasterType(file([...Buffer.from('GIF89a')], 'image/gif')), 'image/gif');
  assert.equal(await rasterType(file([...Buffer.from('RIFF1234WEBP')], 'image/webp')), 'image/webp');
  assert.equal(await rasterType(file([...Buffer.from('<svg/>')], 'image/png')), null);
  assert.equal(await rasterType(file([...Buffer.from('<svg/>')], 'image/svg+xml')), null);
  assert.equal(await rasterType(file([137,80,78,71,13,10,26,10], 'application/pdf')), null);
});

const entry = fileURLToPath(new URL('./chat-attachments.tsx', import.meta.url));
const result = await build({ entryPoints:[entry], bundle:true, write:false, platform:'node', format:'cjs', jsx:'automatic', plugins:[{name:'stubs', setup(b) {
  b.onResolve({filter:/^(react|react\/jsx-runtime|lucide-react)$/}, a => ({path:a.path, namespace:'stub'}));
  b.onResolve({filter:/\.css$/}, () => ({path:'css', namespace:'stub'}));
  b.onLoad({filter:/.*/, namespace:'stub'}, ({path}) => ({loader:'js', contents: path === 'react' ? 'export const useState=(...a)=>globalThis.hooks.useState(...a), useRef=(...a)=>globalThis.hooks.useRef(...a), useEffect=(...a)=>globalThis.hooks.useEffect(...a);' : path === 'react/jsx-runtime' ? 'export const jsx=(type,props,key)=>({type,props,key}), jsxs=jsx;' : path === 'lucide-react' ? 'export const Plus=()=>null, Image=()=>null, Camera=()=>null, FileText=()=>null, X=()=>null;' : ''}));
}}] });
function setup() {
  const slots = [], cleanups = []; let cursor = 0;
  const hooks = {
    useState(initial) { const i=cursor++; if (!(i in slots)) slots[i]=initial; return [slots[i], x => { slots[i]=typeof x === 'function' ? x(slots[i]) : x; }]; },
    useRef(initial) { const i=cursor++; if (!(i in slots)) slots[i]={current:initial}; return slots[i]; },
    useEffect(fn) { const i=cursor++; if (!(i in slots)) { slots[i]=true; cleanups.push(fn()); } },
  };
  const module={exports:{}};
  vm.runInNewContext(result.outputFiles[0].text, { module, exports:module.exports, hooks, Uint8Array, Blob, URL, Promise });
  return { mod:module.exports, render(fn, props) { cursor=0; return fn(props); }, cleanups };
}
function find(n, predicate) { if (!n || typeof n !== 'object') return; if (predicate(n)) return n; for (const c of [n.props?.children].flat(Infinity)) { const r=find(c,predicate); if (r) return r; } }
test('menu + inclui galeria, câmera traseira, arquivo e rejeita lote inválido', () => {
  const ui=setup(); let files=[]; const props={files, onChange:value => {files=value;}, disabled:false};
  let tree=ui.render(ui.mod.AttachmentPicker,props);
  find(tree,n=>n.props?.['aria-label']==='Adicionar anexo').props.onClick();
  tree=ui.render(ui.mod.AttachmentPicker,props);
  assert.ok(find(tree,n=>Array.isArray(n.props?.children) && n.props.children.some(c => typeof c === 'string' && c.includes('Fotos da galeria'))));
  assert.ok(find(tree,n=>Array.isArray(n.props?.children) && n.props.children.some(c => typeof c === 'string' && c.includes('Câmera'))));
  const inputs=[]; (function collect(n) { if (!n || typeof n !== 'object') return; if(n.type==='input') inputs.push(n); for(const c of [n.props?.children].flat(Infinity)) collect(c); })(tree);
  assert.equal(inputs.length,3); assert.equal(inputs[0].props.accept,'image/*'); assert.equal(inputs[1].props.capture,'environment');
  const target={files:[fake('vazio',0)],value:'old'}; inputs[2].props.onChange({currentTarget:target});
  assert.equal(target.value,''); assert.equal(files.length,0);
  tree=ui.render(ui.mod.AttachmentPicker,props); assert.match(find(tree,n=>n.props?.role==='alert').props.children,/vazios/);
  inputs[2].props.onChange({currentTarget:{files:[fake('documento.pdf',10)],value:'old'}}); assert.equal(files[0].name,'documento.pdf');
});
test('avisa que nada foi enviado e permite remover individualmente', () => {
  const ui=setup(), files=[fake('a.pdf',10),fake('b.txt',20)]; let remaining;
  const tree=ui.render(ui.mod.AttachmentPreview,{files,onChange:x=>{remaining=x;}});
  assert.match(find(tree,n=>n.props?.role==='status').props.children,/não podem ser enviados ou analisados/);
  find(tree,n=>n.props?.['aria-label']==='Remover a.pdf').props.onClick();
  assert.equal(remaining.length,1); assert.equal(remaining[0].name,'b.txt');
  ui.cleanups.forEach(fn=>fn?.());
});
