// Run with node --test; exercise the actual chat component with a pending API promise.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import vm from 'node:vm';

const entry = fileURLToPath(new URL('./animated-ai-chat.tsx', import.meta.url));
const result = await build({ entryPoints:[entry], bundle:true, write:false, platform:'node', format:'cjs', jsx:'automatic', plugins:[{name:'stubs',setup(b){
  b.onResolve({filter:/.*/}, a => a.kind === 'entry-point' ? null : ({path:a.path, namespace:'stub'}));
  b.onLoad({filter:/.*/,namespace:'stub'}, ({path}) => {
    if(path==='react') return {contents:'export const useState=(...a)=>hooks.useState(...a), useRef=(...a)=>hooks.useRef(...a), useEffect=(...a)=>hooks.useEffect(...a), useCallback=(f)=>f;',loader:'js'};
    if(path==='react/jsx-runtime') return {contents:'export const jsx=(type,props,key)=>({type,props,key}), jsxs=jsx;',loader:'js'};
    if(path==='lucide-react') return {contents:'export const ArrowUp=()=>null;',loader:'js'};
    if(path==='../../services/api') return {contents:'export const api=globalThis.api;',loader:'js'};
    if(path.includes('chat-attachments')) return {contents:'export function AttachmentPicker(){} export function AttachmentPreview(){}',loader:'js'};
    if(path.includes('request-wait')) return {contents:'export const createWaitTracker=(cb)=>{cb({seconds:0,notice:"Aguardando resposta. As etapas do trabalho não estão disponíveis."});return ()=>{globalThis.stopped.count++}}, formatElapsed=(s)=>`${s} s`;',loader:'js'};
    if(path==='react-markdown' || path==='remark-gfm' || path==='./activity-steps') return {contents:'export default function Stub(){}',loader:'js'};
    if(path.includes('caio-avatar')) return {contents:'export function CaioAvatar(){}',loader:'js'};
    if(path.includes('message-actions')) return {contents:'export function MessageActions(){}',loader:'js'};
    if(path.includes('reply-suggestions')) return {contents:'export function ReplySuggestions(){}',loader:'js'};
    if(path.includes('share-draft')) return {contents:'export function ShareDraft(){}',loader:'js'};
    return {contents:'',loader:'js'};
  });
}}]});
function all(n,predicate,out=[]){if(!n||typeof n!=='object')return out;if(predicate(n))out.push(n);for(const c of [n.props?.children].flat(Infinity))all(c,predicate,out);return out;}
async function setup(){
  const slots=[]; let cursor=0; const effects=[]; let resolve, reject; const stopped={count:0};
  const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
  const hooks={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return [slots[i],x=>{slots[i]=typeof x==='function'?x(slots[i]):x;}];},useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect(cb){const i=cursor++;if(!(i in slots)){slots[i]=true;effects.push(cb);}}};
  const api={sendChatMessage:()=>promise,getChatHistory:()=>Promise.resolve([{role:'assistant',content:'histórico'}])};
  const module={exports:{}};vm.runInNewContext(result.outputFiles[0].text,{module,exports:module.exports,hooks,api,stopped,Promise,Symbol});
  const wrapper=module.exports.AnimatedAIChat({sessionId:'s1',sharedText:null,onSharedTextTaken(){},shareError:false,onDismissShareError(){}});
  const render=()=>{cursor=0;return wrapper.type(wrapper.props);};
  render(); for(const effect of effects)effect(); await Promise.resolve();
  return {render,resolve,reject,stopped};
}
for(const fail of [false,true]) test(`motion follows the real request (${fail?'failure':'success'})`,async()=>{
  const {render,resolve,reject,stopped}=await setup(); let tree=render();
  assert.equal(all(tree,n=>n.type==='article').length,1);
  assert.doesNotMatch(all(tree,n=>n.type==='article')[0].props.className,/chat-message-new/);
  assert.equal(all(tree,n=>n.props?.className==='request-wait-indicator').length,0);
  all(tree,n=>n.type==='textarea'&&n.props.id==='chat-input')[0].props.onChange({target:{value:'Olá'}});
  tree=render(); all(tree,n=>n.type==='form')[0].props.onSubmit({preventDefault(){}});
  tree=render(); const articles=all(tree,n=>n.type==='article');
  assert.match(articles[1].props.className,/chat-message-new/);
  const indicator=all(tree,n=>n.props?.className==='request-wait-indicator');
  assert.equal(indicator.length,1);
  const art=all(tree,n=>n.props?.className==='request-wait-art');
  assert.equal(art.length,1); assert.equal(art[0].props['aria-hidden'],'true');
  assert.equal(all(art[0],n=>n.props?.className?.includes('request-wait-spark')).length,2);
  const statuses=all(tree,n=>n.props?.role==='status');
  assert.equal(statuses.length,1);
  assert.match(statuses[0].props.children,/Aguardando resposta/);
  assert.doesNotMatch(JSON.stringify(tree),/Executando código/i);
  if(fail) reject(new Error('offline')); else resolve({status:'completed',content:'Resposta real'});
  await new Promise(setImmediate);
  tree=render(); assert.equal(stopped.count,1);
  assert.equal(all(tree,n=>n.props?.className==='request-wait-indicator').length,0);
  assert.match(all(tree,n=>n.type==='article')[2].props.className,/chat-message-new/);
});
test('motion respects reduced motion and does not present a progress value',async()=>{
  const css=await readFile(new URL('../../chat.css',import.meta.url),'utf8');
  assert.match(css,/@media \(prefers-reduced-motion:no-preference\)[\s\S]*?\.chat-message-new, \.chat-activity \{ animation:chat-enter \.25s ease-out; \}[\s\S]*?\.request-wait-indicator \{ animation:wait-pulse 1\.1s ease-in-out 2; \}[\s\S]*?\.request-wait-spark \{ animation:wait-spark 2\.4s ease-in-out infinite; \}/);
  assert.match(css,/@media \(prefers-reduced-motion:reduce\)/);
  assert.doesNotMatch(css,/caio-breathe/);
});
