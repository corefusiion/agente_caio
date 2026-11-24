import { useState } from 'react'
import { Cpu, Globe, MessageCircle, Key, Type } from 'lucide-react'

type Props = {
  messageTextSize: 'standard' | 'large'
  onMessageTextSizeChange: (value: 'standard' | 'large') => boolean
}

// Integration configuration remains unavailable here until it can be verified safely.
export default function CoreSettings({ messageTextSize, onMessageTextSizeChange }: Props) {
  const [notice, setNotice] = useState('')
  const changeSize = (value: 'standard' | 'large') => {
    const persisted = onMessageTextSizeChange(value)
    setNotice(persisted
      ? 'Tamanho aplicado e salvo neste navegador.'
      : 'Tamanho aplicado por enquanto, mas não foi possível salvá-lo neste navegador.')
  }

  return (
    <div className="space-y-6 pb-20 max-w-4xl">
      <header>
        <h1 className="text-2xl font-bold text-zinc-900">Preferências</h1>
        <p className="text-sm text-zinc-600 mt-2">Ajustes locais e disponibilidade das configurações do Caio.</p>
      </header>

      <section aria-labelledby="display-title" className="rounded-2xl border border-zinc-200 bg-white p-5 sm:p-6">
        <h2 id="display-title" className="flex items-center gap-2 text-lg font-semibold"><Type aria-hidden="true" className="w-5 h-5" /> Leitura no chat</h2>
        <p id="display-help" className="text-sm text-zinc-600 mt-2">Altera apenas o tamanho do texto das mensagens neste navegador. Não muda o comportamento do Caio.</p>
        <fieldset aria-describedby="display-help" className="mt-5">
          <legend className="text-sm font-semibold text-zinc-800 mb-3">Tamanho das mensagens</legend>
          <div className="flex flex-wrap gap-3">
            <label className="flex items-center gap-2 rounded-xl border border-zinc-300 px-4 py-3 text-sm cursor-pointer">
              <input type="radio" name="message-size" value="standard" checked={messageTextSize === 'standard'} onChange={() => changeSize('standard')} /> Padrão
            </label>
            <label className="flex items-center gap-2 rounded-xl border border-zinc-300 px-4 py-3 text-sm cursor-pointer">
              <input type="radio" name="message-size" value="large" checked={messageTextSize === 'large'} onChange={() => changeSize('large')} /> Maior
            </label>
          </div>
        </fieldset>
        <p role="status" aria-live="polite" className="text-sm text-zinc-700 mt-3 min-h-5">{notice}</p>
      </section>

      <section aria-labelledby="unavailable-title" className="rounded-2xl border border-zinc-200 bg-zinc-50 p-5 sm:p-6">
        <h2 id="unavailable-title" className="text-lg font-semibold">Configurações ainda indisponíveis</h2>
        <p className="text-sm text-zinc-600 mt-2">Estas opções ainda não podem ser configuradas com segurança por esta tela. Nenhuma conta será conectada e nenhuma configuração do agente será alterada aqui.</p>
        <ul className="mt-5 grid gap-3 sm:grid-cols-2">
          {[
            { icon: Cpu, title: 'Modelo de IA e autonomia', desc: 'Escolha de modelo e alterações de comportamento ainda não disponíveis.' },
            { icon: MessageCircle, title: 'Comunicação', desc: 'Conexão e ativação de canais ainda não disponíveis.' },
            { icon: Globe, title: 'Integrações', desc: 'Calendário, busca e serviços externos ainda não podem ser conectados aqui.' },
            { icon: Key, title: 'Credenciais', desc: 'Esta tela não solicita nem salva senhas ou chaves.' },
          ].map(({ icon: Icon, title, desc }) => (
            <li key={title} className="rounded-xl border border-zinc-200 bg-white p-4">
              <h3 className="flex gap-2 items-center text-sm font-semibold text-zinc-800"><Icon aria-hidden="true" className="w-4 h-4" />{title}</h3>
              <p className="text-sm text-zinc-600 mt-2">{desc}</p>
            </li>
          ))}
        </ul>
        <p className="text-sm text-zinc-600 mt-4">A disponibilidade real dos serviços não foi verificada nesta tela.</p>
      </section>
    </div>
  )
}
