import { Link } from '@tanstack/react-router';

import { EmptyState } from '../components/EmptyState';
import { ProductPhoto } from '../components/ProductPhoto';
import { ArrowLeftIcon, ChatIcon, StoreIcon } from '../components/Icons';
import { formatRupiah } from '../lib/format';
import { isWaNumber, type ReviewedProduct } from '../lib/catalog';
import { useT } from '../lib/locale';
import { setStoreName, useCatalog, useSeller } from '../lib/store';

function Column({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-[420px] md:max-w-none px-4 pt-6 pb-10">{children}</div>;
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
  return (
    <span className="kk-money text-price-sm tracking-[-0.03em] break-words text-fg">
      {formatRupiah(product.price_idr)}
    </span>
  );
}

/**
 * The buyer's way in, mirroring the public product page (`/p/{job}/{sku}`): a WhatsApp link to the
 * seller's number with the product pre-filled, or the neutral "contact the store" line without one.
 */
function ChatCta({ product, waNumber }: { product: ReviewedProduct; waNumber: string }) {
  const { t } = useT();
  if (waNumber === '') {
    return (
      <p className="m-0 flex min-h-11 items-center gap-1.5 text-sm text-fg-muted">
        <ChatIcon size={14} />
        {t('preview.contactStore')}
      </p>
    );
  }
  const text = t('preview.chatMessage', { name: product.name, sku: product.sku });
  return (
    <a
      className="kk-btn kk-btn-secondary kk-btn-compact kk-tactile w-full px-2"
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
      <header className="flex items-center gap-4">
        <span className="kk-icon-tile" aria-hidden="true">
          <StoreIcon size={30} />
        </span>
        <div className="min-w-0 flex-1">
          {storeNamed ? (
            <h1 className="text-2xl break-words">{storeName}</h1>
          ) : (
            <h1 className="text-2xl break-words text-fg-muted">{t('preview.defaultStoreName')}</h1>
          )}
          <p className="mt-1 text-sm text-fg-2">
            {storeNamed ? t('review.count', { n: shown.length }) : t('preview.defaultStoreHint')}
          </p>
        </div>
      </header>
      <p className="mt-4 text-base text-fg-2">{t('preview.lead')}</p>

      {shown.length === 0 ? (
        <div className="mt-6">
          <EmptyState text={t('preview.empty')} icon={<StoreIcon size={28} />} />
        </div>
      ) : (
        <ul className="m-0 mt-6 grid list-none grid-cols-2 gap-3 p-0">
          {shown.map((product) => {
            const soldOut = product.availability === 'out of stock';
            return (
              <li
                key={product.sku}
                className={`kk-card m-0 flex min-w-0 flex-col gap-2 p-2 ${soldOut ? 'opacity-60' : ''}`}
              >
                <ProductPhoto src={product.clean_url} className="aspect-square w-full" />
                <h2 className="kk-clamp-2 px-1 text-base break-words">{product.name}</h2>
                <div className="px-1">
                  <PriceLine product={product} />
                </div>
                <p className="kk-clamp-2 px-1 text-xs font-medium text-fg-muted" lang={locale}>
                  {locale === 'id' ? product.description_id : product.description_en}
                </p>
                <div className="mt-auto flex flex-col items-start gap-2 px-1">
                  <span className="kk-pill" data-tone={soldOut ? 'fallback' : 'pass'}>
                    {soldOut ? t('preview.soldOut') : t('preview.available')}
                  </span>
                </div>
                <ChatCta product={product} waNumber={waNumber} />
              </li>
            );
          })}
        </ul>
      )}

      {hidden > 0 ? <p className="mt-4 text-sm text-fg-muted">{t('preview.hidden', { n: hidden })}</p> : null}

      <details className="kk-details kk-card-flat mt-8 px-4 py-1" open={!storeNamed}>
        <summary>{t('preview.sellerTools')}</summary>
        <div className="flex flex-col gap-3 pt-1 pb-3">
          <div>
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
          {waNumber === '' && shown.length > 0 ? (
            <p className="text-sm text-fg-muted">{t('preview.waHint')}</p>
          ) : null}
        </div>
      </details>

      <Link to="/review" className="kk-btn kk-btn-secondary kk-tactile mt-6 w-full">
        <ArrowLeftIcon size={20} />
        {t('preview.backToReview')}
      </Link>
    </Column>
  );
}
