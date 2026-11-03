import { memo, useId } from 'react';

import { gateState, hasPrice, type ReviewedProduct } from '../lib/catalog';
import { formatRupiah, parseRupiahInput } from '../lib/format';
import { useT } from '../lib/locale';
import { editProduct, mergeSkuInto, promoteSkuPhoto, unmergeSku } from '../lib/store';
import { AlertIcon, ChatIcon, CheckIcon, LayersIcon } from './Icons';
import { ProductPhoto } from './ProductPhoto';
import { Switch } from './Switch';

interface MergeTarget {
  sku: string;
  name: string;
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

/** Brandkit §4 Review Card. Name, price, availability, inclusion are editable; the rest is read-only. */
export const ReviewCard = memo(function ReviewCard({
  product,
  suggestion,
  mergedPhotos,
  mergeTargets,
}: ReviewCardProps) {
  const { t, locale } = useT();
  const uid = useId();
  const { sku } = product;
  const mergeSelectId = `${uid}-merge`;

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
        <ProductPhoto src={product.clean_url} className="h-[72px] w-[72px]" />
        <div className="flex min-w-0 flex-1 flex-col justify-between gap-2">
          <textarea
            rows={2}
            className="kk-input resize-none px-3 py-2 font-display text-lg"
            value={product.name}
            aria-label={t('review.nameFor', { sku })}
            onChange={(event) =>
              editProduct(sku, { name: event.target.value.replace(/\s*\n\s*/g, ' ') }, 'debounced')
            }
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                event.currentTarget.blur();
              }
            }}
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
        <section className="flex flex-col gap-2 rounded-[12px] border border-line p-2">
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

      <div className="flex flex-col gap-1 pt-2">
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
        <div className="flex flex-col gap-1">
          <label htmlFor={mergeSelectId} className="text-sm font-bold">
            {t('review.mergeLabel')}
          </label>
          <select
            id={mergeSelectId}
            className="kk-input min-h-11 w-full"
            value=""
            onChange={(event) => {
              if (event.target.value !== '') mergeSkuInto(sku, event.target.value);
            }}
          >
            <option value="">{t('review.mergePlaceholder')}</option>
            {mergeTargets.map((target) => (
              <option key={target.sku} value={target.sku}>
                {target.sku} — {target.name}
              </option>
            ))}
          </select>
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
