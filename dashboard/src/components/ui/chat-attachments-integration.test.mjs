// Run with node --test; hook harness verifies both submit paths fail closed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
const entry = fileURLToPath(new URL('./animated-ai-chat.tsx', import.meta.url));
const result = await build({entryPoints:[entry], bundle:true, write:false, platform:'node', format:'cjs', jsx:'automatic', plugins:[{name:'stubs', setup(b) {
  b.onResolve({filter:/.*/}, a => a.kind === 'entry-point' ? null : ({path:a.path, namespace:'stub'}));
  b.onLoad({filter:/.*/,namespace:'stub'}, ({path}) => {
    if(path==='react') return {contents:'export const useState=(...a)=>hooks.useState(...a), useRef=(...a)=>hooks.useRef(...a), useEffect=(...a)=>hooks.useEffect(...a), useCallback=(f)=>f;',loader:'js'};
    if(path==='react/jsx-runtime') return {contents:'export const jsx=(type,props,key)=>({type,props,key}), jsxs=jsx;',loader:'js'};
    if(path==='lucide-react') return {contents:'export const ArrowUp=()=>null;',loader:'js'};
    if(path==='../../services/api') return {contents:'export const api=globalThis.api;',loader:'js'};
    if(path.includes('chat-attachments')) return {contents:'export function AttachmentPicker(){} export function AttachmentPreview(){}',loader:'js'};
    if(path.includes('request-wait')) return {contents:'export const createWaitTracker=()=>()=>{}, formatElapsed=()=>"";',loader:'js'};
    if(path==='react-markdown' || path==='remark-gfm' || path==='./activity-steps') return {contents:'export default function Stub(){}',loader:'js'};
    if(path.includes('caio-avatar')) return {contents:'export function CaioAvatar(){}',loader:'js'};
    if(path.includes('message-actions')) return {contents:'export function MessageActions(){}',loader:'js'};
    if(path.includes('reply-suggestions')) return {contents:'export function ReplySuggestions(){}',loader:'js'};
    if(path.includes('share-draft')) return {contents:'export function ShareDraft(){}',loader:'js'};
    return {contents:'',loader:'js'};
  });
}}]});
function find(n,predicate){if(!n||typeof n!=='object')return; if(predicate(n))return n; for(const c of [n.props?.children].flat(Infinity)){const r=find(c,predicate);if(r)return r;}}
test('submit e Enter não enviam texto com arquivo; após remoção o texto pode ser enviado', async () => {
  const slots=[]; let cursor=0, calls=[];
  const hooks={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return [slots[i],x=>{slots[i]=typeof x==='function'?x(slots[i]):x;}];},useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect(){cursor++;}};
  const api={sendChatMessage:(...args)=>{calls.push(args);return Promise.resolve({status:'completed',content:'ok'});},getChatHistory:()=>Promise.resolve([])};
  const module={exports:{}};vm.runInNewContext(result.outputFiles[0].text,{module,exports:module.exports,hooks,api,Promise,Symbol});
  const wrapper=module.exports.AnimatedAIChat({sessionId:'s1',sharedText:null,onSharedTextTaken(){},shareError:false,onDismissShareError(){}});
  const render=()=>{cursor=0;return wrapper.type(wrapper.props);};
  let tree=render();
  find(tree,n=>n.type==='textarea'&&n.props.id==='chat-input').props.onChange({target:{value:'Analise este arquivo'}});
  tree=render(); const picker=find(tree,n=>n.type?.name==='AttachmentPicker');
  picker.props.onChange([{name:'foto.jpg',size:3}]);
  tree=render(); assert.equal(find(tree,n=>n.type==='button'&&n.props['aria-label']==='Enviar mensagem').props.disabled,true);
  find(tree,n=>n.type==='form').props.onSubmit({preventDefault(){}});
  find(tree,n=>n.type==='textarea'&&n.props.id==='chat-input').props.onKeyDown({key:'Enter',shiftKey:false,nativeEvent:{isComposing:false},preventDefault(){}});
  assert.equal(calls.length,0);
  find(tree,n=>n.type?.name==='AttachmentPreview').props.onChange([]);
  tree=render(); find(tree,n=>n.type==='form').props.onSubmit({preventDefault(){}});
  assert.equal(calls.length,1); assert.equal(calls[0][0],'Analise este arquivo'); assert.equal(calls[0][1],'s1');
  await Promise.resolve();
});
