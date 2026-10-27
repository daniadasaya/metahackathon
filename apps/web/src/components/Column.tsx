import type { ReactNode } from 'react';

/** The one screen column: same gutters and rhythm on every screen (brandkit §4). */
export function Column({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-[420px] md:max-w-none px-4 pt-4 pb-4">{children}</div>;
}
