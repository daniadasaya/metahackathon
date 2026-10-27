import { useId } from 'react';

interface LogoProps {
  /** Render size in px (the mark is square). */
  size?: number;
  /** Verified dot; hidden below 32px per brandkit §1.1. Defaults to `size >= 32`. */
  showDot?: boolean;
  /** Accessible name; pass an empty string when the wordmark beside it already says it. */
  title?: string;
}

/**
 * The Lightbox Grid mark (brandkit §1.1): three soft tiles, one sharp spark, one verified dot.
 * Every fill reads a semantic token, so one component serves both themes.
 */
export function Logo({ size = 24, showDot, title = '' }: LogoProps) {
  const dot = showDot ?? size >= 32;
  const titleId = useId();
  const labelled = title !== '';

  return (
    <svg
      viewBox="0 0 96 96"
      width={size}
      height={size}
      overflow="visible"
      role={labelled ? 'img' : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-labelledby={labelled ? titleId : undefined}
      focusable="false"
      className="shrink-0"
    >
      {labelled ? <title id={titleId}>{title}</title> : null}
      <rect x="0" y="0" width="44" height="44" rx="9.7" fill="var(--accent-secondary)" />
      <rect x="0" y="52" width="44" height="44" rx="9.7" fill="var(--accent-secondary)" />
      <rect x="52" y="52" width="44" height="44" rx="9.7" fill="var(--accent-secondary)" />
      <path
        d="M74 0 Q78 18 96 22 Q78 26 74 44 Q70 26 52 22 Q70 18 74 0 Z"
        fill="var(--accent-primary)"
      />
      {dot ? (
        <circle
          cx="90"
          cy="90"
          r="8"
          fill="var(--price-verified)"
          stroke="var(--bg-canvas)"
          strokeWidth="3"
        />
      ) : null}
    </svg>
  );
}

interface WordmarkProps {
  name: string;
}

/** Header lockup (brandkit §1.3): 28px mark, 8px gap, Outfit 800 22px (`lg`) wordmark. */
export function Wordmark({ name }: WordmarkProps) {
  return (
    <span className="inline-flex items-center gap-2">
      <Logo size={28} />
      <span className="font-display text-lg font-extrabold text-fg">
        {name}
      </span>
    </span>
  );
}
