interface SkeletonProps {
  /** Tailwind sizing classes, e.g. "h-14 w-full". */
  className?: string;
}

/** Loading placeholder block. Decorative: hidden from assistive tech. */
export function Skeleton({ className = 'h-14 w-full' }: SkeletonProps) {
  return <div aria-hidden="true" className={`rounded-[12px] bg-subtle ${className}`} />;
}
