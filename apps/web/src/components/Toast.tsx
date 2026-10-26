import { useEffect } from 'react';

import { useT } from '../lib/locale';
import { AlertIcon, CloseIcon } from './Icons';

interface ToastProps {
  /** Already translated text. Never a server `message`. */
  text: string;
  onDismiss: () => void;
}

const AUTO_DISMISS_MS = 6000;

/** Error toast: one small component. Announced to assistive tech, dismisses itself. */
export function Toast({ text, onDismiss }: ToastProps) {
  const { t } = useT();

  useEffect(() => {
    const timer = window.setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [text, onDismiss]);

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-28 z-20 flex justify-center px-4">
      <div
        role="alert"
        className="kk-toast pointer-events-auto flex w-full max-w-[392px] items-start gap-3 py-3 pr-1 pl-4"
      >
        <span className="mt-0.5 text-danger">
          <AlertIcon />
        </span>
        <p className="min-w-0 flex-1 py-0.5 text-sm break-words">{text}</p>
        <button
          type="button"
          className="kk-btn kk-btn-ghost"
          aria-label={t('common.close')}
          onClick={onDismiss}
        >
          <CloseIcon size={18} />
        </button>
      </div>
    </div>
  );
}
