import { useState } from 'react';

import { PhotoIcon } from './Icons';

interface ProductPhotoProps {
  src: string;
  /** Tailwind sizing for the frame, e.g. "h-[104px] w-[104px]". */
  className: string;
}

function PhotoFrame({ src, className }: ProductPhotoProps) {
  const [failed, setFailed] = useState(false);
  return (
    <span className={`kk-photo ${className}`}>
      {failed ? (
        <PhotoIcon size={28} />
      ) : (
        <img src={src} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
      )}
    </span>
  );
}

/**
 * A product photo that never shows a broken-image glyph: if the file does not load, a token-styled
 * placeholder takes its place. Keyed on `src`, so a retried photo (new URL) gets a fresh attempt.
 */
export function ProductPhoto({ src, className }: ProductPhotoProps) {
  return <PhotoFrame key={src} src={src} className={className} />;
}
