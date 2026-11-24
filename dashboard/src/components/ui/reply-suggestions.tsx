import './reply-suggestions.css';

/**
 * Exibe três sugestões de texto para continuar a conversa, após uma resposta.
 *
 * - content: texto da resposta (não é copiado para as sugestões nem enviado a serviços).
 * - onSelect: recebe o texto escolhido; o chat decide como enviá-lo.
 * - suggestions: opcional, exatamente três textos curtos já validados pelo chamador.
 *   O componente também verifica formato e duplicatas; se inválidos, usa opções gerais.
 *   Não informe dados sensíveis nem trechos privados nessas opções. A origem e a
 *   pertinência das opções externas devem ser verificadas pelo integrador.
 *
 * Exemplo: <ReplySuggestions content={resposta} onSelect={enviarTexto} />
 * Monte somente para respostas concluídas e bem-sucedidas, quando o envio estiver
 * disponível; o componente não acompanha status ou sessão e não executa ações.
 */
export type ReplySuggestionsProps = {
  content: string;
  onSelect: (text: string) => void;
  suggestions?: readonly string[];
};

const GENERAL = [
  'Resuma essa resposta',
  'Explique melhor',
  'Quais são os próximos passos?',
] as const;

// Match broad topics locally; never reuse the answer or extract personal data.
function generalSuggestions(content: string): readonly string[] {
  const text = content.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  if (/\b(preco|precos|custo|custos|orcamento|valores|valor|gasto|gastos)\b/.test(text)) {
    return ['Resuma os custos', 'Quais são as alternativas?', 'Explique os valores'];
  }
  if (/\b(passo a passo|etapas|planejamento|plano|cronograma)\b/.test(text)) {
    return ['Resuma as etapas', 'Por onde começar?', 'Quais são os cuidados?'];
  }
  if (/\b(receita|ingredientes|preparo)\b/.test(text)) {
    return ['Resuma o preparo', 'Quais são as substituições?', 'Como ajustar as porções?'];
  }
  return GENERAL;
}

function validatedSuggestions(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.length !== 3) return null;
  const result: string[] = [];
  for (const item of value) {
    if (typeof item !== 'string') return null;
    const text = item.trim();
    // Reject controls and directional overrides, multiline text and oversized labels.
    if (!text || [...text].length > 80 || /[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]/.test(text)) return null;
    result.push(text);
  }
  if (new Set(result.map(text => text.toLocaleLowerCase('pt-BR'))).size !== 3) return null;
  return result;
}

export function ReplySuggestions({ content, onSelect, suggestions }: ReplySuggestionsProps) {
  if (typeof content !== 'string' || !content.trim()) return null;
  const supplied = validatedSuggestions(suggestions);
  const options = supplied ?? generalSuggestions(content);
  const label = supplied ? 'Sugestões' : 'Sugestões gerais';

  return (
    <section className="reply-suggestions" aria-label={label}>
      <p className="reply-suggestions__label">{label}</p>
      <div className="reply-suggestions__options">
        {options.map(text => (
          <button className="reply-suggestions__button" type="button" key={text} onClick={() => onSelect(text)}>
            {text}
          </button>
        ))}
      </div>
    </section>
  );
}

export default ReplySuggestions;
