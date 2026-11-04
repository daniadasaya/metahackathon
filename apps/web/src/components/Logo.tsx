/** A compact bolt mark, with its name supplied by the adjacent wordmark. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" focusable="false" className="shrink-0">
      <rect width="32" height="32" rx="9" fill="var(--text-primary)" />
      <path d="M18.5 5 9 17h6l-1.5 10L23 14h-6z" fill="var(--bg-canvas)" />
    </svg>
  );
}

export function Wordmark({ name }: { name: string }) {
  return <span className="inline-flex items-center gap-2"><Logo /><span>{name}</span></span>;
}
