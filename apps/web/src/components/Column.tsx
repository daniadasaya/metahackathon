import type { ReactNode } from 'react';

/** Shared screen gutters and vertical rhythm. */
export function Column({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-(--content-max-width) md:max-w-none px-4 pt-4 pb-4">{children}</div>;
}
