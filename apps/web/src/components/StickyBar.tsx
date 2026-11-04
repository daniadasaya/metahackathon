import type { ReactNode } from 'react';

/** Sticky bottom bar: stays above the safe area, always reachable at 360×800. Content is passed in. */
export function StickyBar({ children }: { children: ReactNode }) {
  return (
    <div className="kk-bar">
      <div className="mx-auto flex w-full max-w-(--action-max-width) md:max-w-none flex-col gap-2">{children}</div>
    </div>
  );
}
