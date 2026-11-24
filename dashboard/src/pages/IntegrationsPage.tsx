import { CalendarDays, Database, Github, Mail, Plug } from 'lucide-react'

// This is a catalog, not a connection-status source. Do not add connection
// actions until a secure, user-isolated backend and status API are verified.
const integrations = [
  { name: 'Gmail', description: 'E-mails', icon: Mail },
  { name: 'Google Calendar', description: 'Agenda e eventos', icon: CalendarDays },
  { name: 'Google Drive', description: 'Arquivos', icon: Database },
  { name: 'GitHub', description: 'Repositórios', icon: Github },
]

export default function IntegrationsPage() {
  return (
    <div className="space-y-6 pb-20 max-w-4xl">
      <header>
        <h1 className="text-2xl font-bold text-zinc-900">Integrações</h1>
        <p className="text-sm text-zinc-600 mt-2">Serviços que poderão ser integrados ao Caio.</p>
      </header>

      <section aria-labelledby="integrations-notice-title" className="rounded-2xl border border-zinc-200 bg-zinc-50 p-5 sm:p-6">
        <h2 id="integrations-notice-title" className="flex items-center gap-2 text-base font-semibold text-zinc-900"><Plug aria-hidden="true" className="w-5 h-5" /> Conexões indisponíveis nesta tela</h2>
        <p id="integrations-help" className="text-sm text-zinc-600 mt-2 leading-relaxed">A conexão ainda precisa de uma implementação segura e isolada por usuário. Esta tela não verifica contas nem o estado das conexões e não solicita credenciais.</p>
      </section>

      <section aria-labelledby="integrations-catalog-title">
        <h2 id="integrations-catalog-title" className="text-lg font-semibold text-zinc-900">Catálogo de serviços</h2>
        <p className="text-sm text-zinc-600 mt-1">Catálogo ilustrativo; disponibilidade e estado não verificados.</p>
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {integrations.map(({ name, description, icon: Icon }) => (
            <li key={name} className="rounded-2xl border border-zinc-200 bg-white p-5 flex flex-col gap-4 min-w-0">
              <div className="flex items-start gap-3">
                <span className="shrink-0 rounded-xl bg-zinc-100 p-2.5 text-zinc-700"><Icon aria-hidden="true" className="w-5 h-5" /></span>
                <div className="min-w-0"><h3 className="text-sm font-semibold text-zinc-900">{name}</h3><p className="text-sm text-zinc-600 mt-1">{description}</p></div>
              </div>
              <div className="mt-auto flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs font-medium text-zinc-600">Estado não verificado</span>
                <button type="button" disabled aria-describedby="integrations-help" aria-label={`Conectar ${name}: indisponível`} className="rounded-lg border border-zinc-200 bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-500 cursor-not-allowed">Conectar</button>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
