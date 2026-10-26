import type { ReactNode } from 'react';

interface EmptyStateProps {
  text: string;
  /** Optional action, e.g. a button back to Upload. */
  children?: ReactNode;
}

/** Empty state: one small component. Copy is passed in already translated. */
export function EmptyState({ text, children }: EmptyStateProps) {
  return (
    <div className="kk-card-flat flex flex-col items-center gap-3 px-4 py-6 text-center">
      <p className="text-base text-fg-2">{text}</p>
      {children}
    </div>
  );
}
