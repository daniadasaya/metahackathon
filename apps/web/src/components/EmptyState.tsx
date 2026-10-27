import type { ReactNode } from 'react';

interface EmptyStateProps {
  text: string;
  /** Optional icon, composed from `Icons.tsx` / `Logo.tsx`. Decorative: the text carries the meaning. */
  icon?: ReactNode;
  /** Optional action, e.g. a button back to Upload. */
  children?: ReactNode;
}

/** Empty state: one small component. Copy is passed in already translated. */
export function EmptyState({ text, icon, children }: EmptyStateProps) {
  return (
    <div className="kk-card-flat flex flex-col items-center gap-3 px-5 py-6 text-center">
      {icon !== undefined ? (
        <span className="kk-empty-icon" aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <p className="max-w-[28ch] text-base text-fg-2">{text}</p>
      {children}
    </div>
  );
}
