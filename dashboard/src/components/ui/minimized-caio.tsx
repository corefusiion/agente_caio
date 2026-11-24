import type { RefObject } from 'react';
import { CaioAvatar } from './caio-avatar';
import './minimized-caio.css';

/** This launcher is part of the page, not a system overlay or an activity indicator. */
export function MinimizedCaio({ buttonRef, onReopen }: {
  buttonRef: RefObject<HTMLButtonElement | null>;
  onReopen: () => void;
}) {
  return (
    <button ref={buttonRef} type="button" className="caio-page-launcher" onClick={onReopen}
      aria-label="Reabrir Caio nesta página" title="Reabrir Caio nesta página">
      <span className="caio-page-launcher-avatar"><CaioAvatar /></span>
    </button>
  );
}
