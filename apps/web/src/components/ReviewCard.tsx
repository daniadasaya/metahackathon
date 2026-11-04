import { memo, useId, useLayoutEffect, useRef, useState } from 'react';

import { gateState, hasPrice, type ReviewedProduct } from '../lib/catalog';
import { formatRupiah, parseRupiahInput } from '../lib/format';
import { useT } from '../lib/locale';
import { editProduct, mergeSkuInto, promoteSkuPhoto, unmergeSku } from '../lib/store';
import { AlertIcon, ChatIcon, CheckIcon, LayersIcon } from './Icons';
import { ProductPhoto } from './ProductPhoto';
import { Switch } from './Switch';
import { BottomSheet } from './BottomSheet';

interface MergeTarget {
  sku: string;
  name: string;
}

/**
 * The product-name field grows with its content. It starts at two rows and never shrinks below that,
 * so a long name is read in full instead of scrolling inside a two-line box. The font size and the
 * colours are unchanged: only the height follows `scrollHeight`.
 */
function NameField({ value, label, onCommit }: { value: string; label: string; onCommit: (next: string) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el === null) return;
    function resize() {
      if (el === null) return;
      el.style.height = 'auto';
      const borders = el.offsetHeight - el.clientHeight;
      el.style.height = `${el.scrollHeight + borders}px`;
    }
    resize();
    let previousWidth = el.parentElement?.clientWidth;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      const width = el.parentElement?.clientWidth;
      if (width !== previousWidth) {
        previousWidth = width;
        window.cancelAnimationFrame(frame);
        frame = window.requestAnimationFrame(resize);
      }
    });
    if (el.parentElement !== null) observer.observe(el.parentElement);
    return () => { observer.disconnect(); window.cancelAnimationFrame(frame); };
  }, [value]);

  return (
    <textarea
      ref={ref}
      rows={2}
      className="kk-input kk-name resize-none"
      value={value}
      aria-label={label}
      onChange={(event) => onCommit(event.target.value.replace(/\s*\n\s*/g, ' '))}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          event.currentTarget.blur();
        }
      }}
    />
  );
}

interface ReviewCardProps {
  product: ReviewedProduct;
  /**
   * The AI's price guess, for grey helper text only. It comes from the live job, never from
   * `kk:catalog`, and nothing on this card writes it anywhere.
   */
  suggestion: number | null;
  /** Angle photos merged into this product. They are not rows of their own. */
  mergedPhotos: readonly ReviewedProduct[];
  /** Included products this card may absorb as an angle photo. */
  mergeTargets: readonly MergeTarget[];
}

/** Name, price, availability and inclusion are editable; the rest is read-only. */
export const ReviewCard = memo(function ReviewCard({
  product,
  suggestion,
  mergedPhotos,
  mergeTargets,
}: ReviewCardProps) {
  const { t, locale } = useT();
  const uid = useId();
  const { sku } = product;
  const [mergeOpen, setMergeOpen] = useState(false);

  const gate = gateState(product);
  const viaChat = product.price_via_chat;
  const confirmed = !viaChat && product.price_idr !== null;
  const missing = !hasPrice(product);
  const priceState = viaChat ? 'chat' : confirmed ? 'confirmed' : 'missing';
  const helperId = `${uid}-helper`;

  const details: Array<[string, string]> = [[t('review.category'), product.category]];
  if (product.brand !== null) details.push([t('review.brand'), product.brand]);
  details.push([
    t('review.condition'),
    product.condition === 'new' ? t('review.conditionNew') : t('review.conditionUsed'),
  ]);
  if (product.attributes.color !== null) details.push([t('review.attrColor'), product.attributes.color]);
  if (product.attributes.size !== null) details.push([t('review.attrSize'), product.attributes.size]);
  if (product.attributes.variant !== null) details.push([t('review.attrVariant'), product.attributes.variant]);

  return (
    <li
      id={`card-${sku}`}
      className="kk-card kk-review m-0 flex scroll-mt-20 list-none flex-col gap-3 p-4"
      data-included={product.included}
      data-price-state={priceState}
    >
      <div className="flex gap-3">
        <ProductPhoto src={product.clean_url} className="h-18 w-18" />
        <div className="flex min-w-0 flex-1 flex-col justify-between gap-2">
          <NameField
            value={product.name}
            label={t('review.nameFor', { sku })}
            onCommit={(next) => editProduct(sku, { name: next }, 'debounced')}
          />
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="kk-pill" data-tone={gate === 'pass' ? 'pass' : 'fallback'}>
              {gate === 'pass' ? <CheckIcon size={14} /> : <LayersIcon size={14} />}
              {gate === 'pass' ? t('review.gatePass') : t('review.gateFallback')}
            </span>
            <span className="kk-money text-xs font-bold text-fg-2">{sku}</span>
            {product.included ? null : (
              <span className="kk-pill" data-tone="neutral">
                {t('review.excludedBadge')}
              </span>
            )}
            {mergedPhotos.length > 0 ? (
              <span className="kk-pill" data-tone="neutral">
                <LayersIcon size={14} />
                {t('review.photosCount', { n: 1 + mergedPhotos.length })}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      {mergedPhotos.length > 0 ? (
        <section className="flex flex-col gap-2 rounded-(--r-sm) border border-line p-2">
          <h3 className="m-0 text-xs font-bold text-fg-muted">
            {t('review.mergedPhotosTitle', { n: mergedPhotos.length })}
          </h3>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {mergedPhotos.map((child) => (
              <li key={child.sku} className="flex flex-wrap items-center gap-2">
                <ProductPhoto src={child.clean_url} className="h-12 w-12 shrink-0" />
                <span className="kk-money min-w-0 flex-1 text-xs font-bold text-fg-2 break-all">{child.sku}</span>
                <button
                  type="button"
                  className="kk-chip kk-tactile min-h-11 px-3"
                  aria-label={t('review.makePrimaryPhotoLabel', { sku: child.sku })}
                  onClick={() => promoteSkuPhoto(sku, child.sku)}
                >
                  {t('review.makePrimaryPhoto')}
                </button>
                <button
                  type="button"
                  className="kk-chip kk-tactile min-h-11 px-3"
                  aria-label={t('review.unmergePhotoLabel', { sku: child.sku })}
                  onClick={() => unmergeSku(child.sku)}
                >
                  {t('review.unmergePhoto')}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {viaChat ? null : (
        <div>
          <div className="kk-price" data-state={confirmed ? 'confirmed' : 'missing'}>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="off"
              enterKeyHint="done"
              className="kk-price-input"
              value={product.price_idr === null ? '' : formatRupiah(product.price_idr)}
              placeholder={t('review.pricePlaceholder')}
              aria-label={t('review.priceFor', { sku })}
              aria-describedby={helperId}
              onChange={(event) => {
                const amount = parseRupiahInput(event.target.value);
                if (amount !== undefined) editProduct(sku, { price_idr: amount }, 'debounced');
              }}
            />
            {confirmed ? (
              <span className="kk-disc" role="img" aria-label={t('review.priceConfirmed')}>
                <CheckIcon size={14} />
              </span>
            ) : null}
          </div>
          <div id={helperId} className="mt-1 flex flex-col px-1">
            {missing ? (
              <p className="flex items-center gap-2 text-sm font-bold text-warning">
                <AlertIcon size={16} />
                {t('review.noPrice')}
              </p>
            ) : null}
            {suggestion !== null ? (
              <p className="text-base text-price-ai">
                {t('review.aiSuggestion', { price: formatRupiah(suggestion) })}
              </p>
            ) : null}
          </div>
        </div>
      )}

      <button
        type="button"
        className="kk-chip kk-chip-wide kk-tactile"
        aria-pressed={viaChat}
        onClick={() => editProduct(sku, { price_via_chat: !viaChat }, 'now')}
      >
        {viaChat ? <CheckIcon size={16} /> : <ChatIcon size={16} />}
        {t('review.viaChat')}
      </button>

      <div className="kk-review-switches flex flex-col">
        <div className="flex items-center justify-between gap-3">
          <span id={`${uid}-stock`} className="text-sm font-bold">
            {t('review.inStock')}
          </span>
          <Switch
            checked={product.availability === 'in stock'}
            labelledBy={`${uid}-stock`}
            onChange={(next) => editProduct(sku, { availability: next ? 'in stock' : 'out of stock' }, 'now')}
          />
        </div>
        <div className="flex items-center justify-between gap-3">
          <span id={`${uid}-included`} className="text-sm font-bold">
            {t('review.included')}
          </span>
          <Switch
            checked={product.included}
            labelledBy={`${uid}-included`}
            onChange={(next) => editProduct(sku, { included: next }, 'now')}
          />
        </div>
      </div>

      {mergeTargets.length > 0 ? (
        <div>
          <button type="button" className="kk-btn kk-btn-secondary w-full" aria-haspopup="dialog" onClick={() => setMergeOpen(true)}>{t('review.mergeLabel')}</button>
          {mergeOpen ? <BottomSheet title={t('review.mergePlaceholder')} onClose={() => setMergeOpen(false)}>
            <div className="kk-sheet-list flex flex-col gap-2">
              {mergeTargets.map((target) => <button key={target.sku} type="button" className="kk-btn kk-btn-secondary w-full" onClick={() => { mergeSkuInto(sku, target.sku); setMergeOpen(false); }}>{target.sku} — {target.name}</button>)}
            </div>
          </BottomSheet> : null}
        </div>
      ) : null}

      <details className="kk-details">
        <summary>{t('review.details')}</summary>
        <div className="flex flex-col gap-3 pt-1 pb-1 text-base">
          <div>
            <p className="text-sm font-bold text-fg-muted">{t('header.languageId')}</p>
            <p lang="id" className="text-fg-2">
              {product.description_id}
            </p>
          </div>
          <div>
            <p className="text-sm font-bold text-fg-muted">{t('header.languageEn')}</p>
            <p lang="en" className="text-fg-2">
              {product.description_en}
            </p>
          </div>
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1" lang={locale}>
            {details.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-sm text-fg-muted">{label}</dt>
                <dd className="m-0 min-w-0 break-words text-fg">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </details>
    </li>
  );
});
