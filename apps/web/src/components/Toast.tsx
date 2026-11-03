import { useEffect } from 'react';

import { useT } from '../lib/locale';
import { AlertIcon, CloseIcon } from './Icons';

interface ToastProps {
  /** Already translated text. Never a server `message`. */
  text: string;
  onDismiss: () => void;
}

const AUTO_DISMISS_MS = 6000;

/**
 * Error alert. It sits inside the sticky bar above the primary action, so it never covers the
 * action. Announced to assistive tech; dismisses itself after six seconds.
 */
export function Toast({ text, onDismiss }: ToastProps) {
  const { t } = useT();

  useEffect(() => {
    const timer = window.setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [text, onDismiss]);

  return (
    <div role="alert" className="kk-toast flex items-start gap-2 py-1 pr-1 pl-3">
      <span className="mt-2 text-danger">
        <AlertIcon />
      </span>
      <p className="min-w-0 flex-1 py-2 text-sm break-words">{text}</p>
      <button type="button" className="kk-btn kk-btn-ghost" aria-label={t('common.close')} onClick={onDismiss}>
        <CloseIcon size={16} />
      </button>
    </div>
  );
}
