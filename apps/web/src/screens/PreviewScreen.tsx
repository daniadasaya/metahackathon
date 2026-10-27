import { Link } from '@tanstack/react-router';

import { EmptyState } from '../components/EmptyState';
import { ProductPhoto } from '../components/ProductPhoto';
import { ChatIcon } from '../components/Icons';
import { formatRupiah } from '../lib/format';
import { isWaNumber, type ReviewedProduct } from '../lib/catalog';
import { useT } from '../lib/locale';
import { setStoreName, useCatalog, useSeller } from '../lib/store';

function Column({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-[420px] px-4 pt-6 pb-16">{children}</div>;
}

function PriceLine({ product }: { product: ReviewedProduct }) {
  const { t } = useT();
  if (product.price_via_chat) {
    return (
      <span className="kk-pill" data-tone="neutral">
        <ChatIcon size={14} />
        {t('review.viaChat')}
      </span>
    );
  }
  if (product.price_idr === null) {
    return <span className="text-sm font-bold text-warning">{t('review.noPrice')}</span>;
  }
  return <span className="kk-money text-price text-fg">{formatRupiah(product.price_idr)}</span>;
}

/**
 * The buyer's way in, mirroring the public product page (`/p/{job}/{sku}`): a WhatsApp link to the
 * seller's number with the product pre-filled, or the neutral "contact the store" line without one.
 */
function ChatCta({ product, waNumber }: { product: ReviewedProduct; waNumber: string }) {
  const { t } = useT();
  if (waNumber === '') {
    return (
      <p className="m-0 flex items-center gap-1 text-sm text-fg-muted">
        <ChatIcon size={14} />
        {t('preview.contactStore')}
      </p>
    );
  }
  const text = t('preview.chatMessage', { name: product.name, sku: product.sku });
  return (
    <a
      className="kk-btn kk-btn-secondary kk-tactile min-h-11 w-full px-2 text-sm"
      href={`https://wa.me/${waNumber}?text=${encodeURIComponent(text)}`}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={t('preview.chatLabel', { name: product.name })}
    >
      <ChatIcon size={16} />
      {t('preview.chat')}
    </a>
  );
}

/** Screen 4: a store-style grid built from the reviewed data in `kk:catalog`. */
export function PreviewScreen() {
  const { t, locale } = useT();
  const catalog = useCatalog();
  const seller = useSeller();

  const shown = (catalog ?? []).filter((product) => product.included);
  const hidden = (catalog?.length ?? 0) - shown.length;
  const storeName = seller?.store_name ?? '';
  const storeNamed = storeName.trim() !== '';
  const sellerWa = seller?.wa_number ?? '';
  const waNumber = isWaNumber(sellerWa) ? sellerWa : '';

  return (
    <Column>
      {storeNamed ? (
        <h1 className="text-2xl break-words">{storeName}</h1>
      ) : (
        <h1 className="text-2xl break-words text-fg-muted">
          <span className="italic">{t('preview.defaultStoreName')}</span>{' '}
          <span className="text-base font-normal">{t('preview.defaultStoreHint')}</span>
        </h1>
      )}
      <p className="mt-2 text-base text-fg-2">{t('preview.lead')}</p>

      <div className="mt-4">
        <label htmlFor="store-name" className="text-sm font-bold">
          {t('preview.storeNameLabel')}
        </label>
        <input
          id="store-name"
          type="text"
          className="kk-input mt-2"
          maxLength={60}
          value={storeName}
          placeholder={t('preview.storeNamePlaceholder')}
          onChange={(event) => setStoreName(event.target.value)}
        />
      </div>

      <Link to="/review" className="kk-btn kk-btn-secondary kk-tactile mt-4 w-full">
        {t('preview.backToReview')}
      </Link>

      {hidden > 0 ? <p className="mt-4 text-sm text-fg-muted">{t('preview.hidden', { n: hidden })}</p> : null}
      {waNumber === '' && shown.length > 0 ? (
        <p className="mt-4 text-sm text-fg-muted">{t('preview.waHint')}</p>
      ) : null}

      {shown.length === 0 ? (
        <div className="mt-5">
          <EmptyState text={t('preview.empty')} />
        </div>
      ) : (
        <ul className="m-0 mt-5 grid list-none grid-cols-2 gap-3 p-0">
          {shown.map((product) => {
            const soldOut = product.availability === 'out of stock';
            return (
              <li key={product.sku} className="kk-card m-0 flex min-w-0 flex-col gap-2 p-2">
                <ProductPhoto src={product.clean_url} className="aspect-square w-full" />
                <h2 className="kk-clamp-2 px-1 text-base leading-5 break-words">{product.name}</h2>
                <p className="kk-clamp-2 px-1 text-sm text-fg-2" lang={locale}>
                  {locale === 'id' ? product.description_id : product.description_en}
                </p>
                <div className="mt-auto flex flex-col items-start gap-2 px-1 pb-1">
                  <PriceLine product={product} />
                  <span className="kk-pill" data-tone={soldOut ? 'fallback' : 'pass'}>
                    {soldOut ? t('preview.soldOut') : t('preview.available')}
                  </span>
                </div>
                <div className="px-1 pb-1">
                  <ChatCta product={product} waNumber={waNumber} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Column>
  );
}
