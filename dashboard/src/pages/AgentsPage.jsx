import { useState } from 'react';
import { Search, Folder, Globe, Mail, Calendar, Workflow, Sparkles } from 'lucide-react';

// Illustrative categories, not live capabilities or evidence of enabled tools.
const categories = [
  { name: 'Arquivos', icon: Folder },
  { name: 'Busca na web', icon: Globe },
  { name: 'E-mail', icon: Mail },
  { name: 'Calendário', icon: Calendar },
  { name: 'Automação', icon: Workflow },
  { name: 'Ferramentas externas', icon: Sparkles },
];

export default function AgentsPage() {
  const [search, setSearch] = useState('');
  const filtered = categories.filter(a => a.name.toLocaleLowerCase('pt-BR').includes(search.trim().toLocaleLowerCase('pt-BR')));
  return (
    <div className="space-y-6 pb-10">
      <header>
        <h1 className="text-2xl font-bold text-zinc-900">Habilidades</h1>
        <p className="text-sm text-zinc-600 mt-2">Categorias ilustrativas. Esta tela não verifica o estado real nem habilita ferramentas.</p>
      </header>
      <div className="relative max-w-sm">
        <Search aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
        <label htmlFor="skill-search" className="sr-only">Buscar categorias de habilidades</label>
        <input id="skill-search" type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Buscar categorias"
          className="w-full pl-10 pr-3 py-3 rounded-xl bg-zinc-100 border border-zinc-200 text-sm text-zinc-800" />
      </div>
      <p className="text-sm text-zinc-600" role="status">{filtered.length} {filtered.length === 1 ? 'categoria' : 'categorias'}</p>
      {filtered.length ? <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map(({ name, icon: Icon }) => <li key={name} className="rounded-2xl border border-zinc-200 p-4 bg-white">
          <Icon aria-hidden="true" className="w-5 h-5 text-zinc-600 mb-3" />
          <h2 className="text-sm font-semibold text-zinc-900">{name}</h2>
          <p className="text-sm text-zinc-600 mt-2">Disponibilidade não verificada</p>
        </li>)}
      </ul> : <p className="text-sm text-zinc-700">Nenhuma categoria encontrada.</p>}
      <p className="text-sm text-zinc-600">Ativação de habilidades por esta interface ainda não está disponível.</p>
    </div>
  );
}
