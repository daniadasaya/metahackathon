import { useEffect, useId, useRef, type ReactNode } from 'react';
import { useT } from '../lib/locale';
import { CloseIcon } from './Icons';

export function BottomSheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const { t } = useT();
  const labelId = useId();
  const sheet = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    sheet.current?.querySelector<HTMLButtonElement>('button')?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== 'Tab' || sheet.current === null) return;
      const controls = Array.from(sheet.current.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)'));
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, []);

  return (
    <div className="kk-sheet-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={sheet} className="kk-sheet" role="dialog" aria-modal="true" aria-labelledby={labelId}>
        <div className="kk-sheet-grabber" aria-hidden="true" />
        <div className="kk-sheet-header">
          <h2 id={labelId} className="kk-sheet-title">{title}</h2>
          <button type="button" className="kk-btn kk-btn-ghost kk-btn-icon" aria-label={t('common.close')} onClick={onClose}><CloseIcon /></button>
        </div>
        {children}
      </div>
    </div>
  );
}
