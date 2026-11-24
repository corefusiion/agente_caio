// This preference affects only message text in this browser, never the agent.
export const DISPLAY_KEY = 'caio_message_text_size';
const valid = value => value === 'large' || value === 'standard';

export function readMessageTextSize(storage) {
  try {
    const value = storage.getItem(DISPLAY_KEY);
    return valid(value) ? value : 'standard';
  } catch {
    return 'standard';
  }
}

export function writeMessageTextSize(storage, value) {
  if (!valid(value)) return false;
  try {
    storage.setItem(DISPLAY_KEY, value);
    return true;
  } catch {
    return false;
  }
}
