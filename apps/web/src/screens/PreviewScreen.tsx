import { useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { BottomSheet } from '../components/BottomSheet';
import { Column } from '../components/Column';
import { EmptyState } from '../components/EmptyState';
import { ProductPhoto } from '../components/ProductPhoto';
import { StickyBar } from '../components/StickyBar';
import { ChatIcon, StoreIcon } from '../components/Icons';
import { formatRupiah } from '../lib/format';
import { canExport, isMergedChild, isWaNumber, mergedChildrenByParent, readiness, type ReviewedProduct } from '../lib/catalog';
import { useT } from '../lib/locale';
import { setStoreName, useCatalog, useSeller } from '../lib/store';

function PhotoGallery({ product, mergedPhotos }: { product: ReviewedProduct; mergedPhotos: readonly ReviewedProduct[] }) {
  const { t } = useT();
  const [index, setIndex] = useState(0);
  const allUrls = [product.clean_url, ...mergedPhotos.map((item) => item.clean_url)];
  return (
    <div className="relative">
      <ProductPhoto src={allUrls[index % allUrls.length] ?? product.clean_url} className="aspect-square w-full" />
      {allUrls.length > 1 ? <button type="button" className="kk-chip kk-tactile absolute right-1 bottom-1 min-w-11 px-2" aria-label={t('review.nextPhotoLabel', { name: product.name })} onClick={() => setIndex((value) => (value + 1) % allUrls.length)}>{`${(index % allUrls.length) + 1}/${allUrls.length}`}</button> : null}
    </div>
  );
}

function PriceLine({ product }: { product: ReviewedProduct }) {
  const { t } = useT();
  if (product.price_via_chat) return <span className="text-sm font-bold text-fg-2">{t('review.viaChat')}</span>;
  if (product.price_idr === null) return <span className="text-sm font-bold text-warning">{t('review.noPrice')}</span>;
  return <span className="kk-money text-base font-bold">{formatRupiah(product.price_idr)}</span>;
}

function ChatCta({ product, waNumber }: { product: ReviewedProduct; waNumber: string }) {
  const { t } = useT();
  if (waNumber === '') return <p className="flex min-h-11 items-center gap-2 text-sm text-fg-muted"><ChatIcon size={16} />{t('preview.contactStore')}</p>;
  const text = t('preview.chatMessage', { name: product.name, sku: product.sku });
  return <a className="kk-btn kk-btn-secondary kk-tactile w-full" href={`https://wa.me/${waNumber}?text=${encodeURIComponent(text)}`} target="_blank" rel="noopener noreferrer" aria-label={t('preview.chatLabel', { name: product.name })}><ChatIcon size={16} />{t('preview.chat')}</a>;
}

/** A shop grid and product detail, using the reviewed catalog in local storage. */
export function PreviewScreen() {
  const { t, locale } = useT();
  const navigate = useNavigate();
  const catalog = useCatalog();
  const seller = useSeller();
  const [selectedSku, setSelectedSku] = useState<string | null>(null);
  const products = catalog ?? [];
  const shown = products.filter((product) => product.included);
  const selected = shown.find((product) => product.sku === selectedSku);
  const childrenMap = mergedChildrenByParent(products);
  const hidden = products.filter((product) => !product.included && !isMergedChild(product, products)).length;
  const storeName = seller?.store_name ?? '';
  const storeNamed = storeName.trim() !== '';
  const sellerWa = seller?.wa_number ?? '';
  const waNumber = isWaNumber(sellerWa) ? sellerWa : '';
  const state = readiness(products);
  const ready = canExport(state);

  return (
    <>
      <Column>
        <header className="flex items-center gap-3">
          <span className="kk-icon-tile" aria-hidden="true"><StoreIcon size={22} /></span>
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl break-words">{storeNamed ? storeName : t('preview.defaultStoreName')}</h1>
            <p className="text-sm text-fg-2">{t('review.count', { n: shown.length })}</p>
          </div>
        </header>
        {shown.length === 0 ? (
          <div className="mt-5"><EmptyState text={t('preview.empty')} icon={<StoreIcon size={24} />}><Link to="/review" className="kk-btn kk-btn-secondary">{t('preview.backToReview')}</Link></EmptyState></div>
        ) : (
          <ul className="m-0 mt-5 grid list-none grid-cols-2 gap-x-3 gap-y-5 p-0">
            {shown.map((product) => (
              <li key={product.sku} className="min-w-0">
                <button type="button" className="kk-preview-tile w-full" onClick={() => setSelectedSku(product.sku)} aria-label={t('preview.openProduct', { name: product.name })}>
                  <ProductPhoto src={product.clean_url} className="aspect-square w-full" />
                  <span className="text-base font-bold break-words">{product.name}</span>
                  <PriceLine product={product} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {hidden > 0 ? <p className="mt-4 text-sm text-fg-muted">{t('preview.hidden', { n: hidden })}</p> : null}
        <details className="kk-details kk-card-flat mt-5 px-4" open={!storeNamed}>
          <summary>{t('preview.sellerTools')}</summary>
          <div className="flex flex-col gap-3 pb-4">
            <label htmlFor="store-name" className="text-sm font-bold">{t('preview.storeNameLabel')}</label>
            <input id="store-name" type="text" className="kk-input" maxLength={60} value={storeName} placeholder={t('preview.storeNamePlaceholder')} onChange={(event) => setStoreName(event.target.value)} />
            {waNumber === '' && shown.length > 0 ? <p className="text-sm text-fg-muted">{t('preview.waHint')}</p> : null}
          </div>
        </details>
      </Column>
      {shown.length > 0 ? <StickyBar>
        {!ready ? <p className="text-center text-sm text-fg-2">{t('review.summary', { n: state.ready, m: state.blockers.length })}</p> : null}
        <button type="button" className="kk-btn kk-btn-primary kk-tactile w-full" aria-disabled={!ready} onClick={() => { if (ready) void navigate({ to: '/export' }); else void navigate({ to: '/review' }); }}>{t('preview.continueExport')}</button>
      </StickyBar> : null}
      {selected !== undefined ? (
        <BottomSheet title={t('preview.productDetails')} onClose={() => setSelectedSku(null)}>
          <PhotoGallery product={selected} mergedPhotos={childrenMap.get(selected.sku) ?? []} />
          <div className="mt-4 flex flex-col gap-3">
            <h3 className="text-xl break-words">{selected.name}</h3>
            <PriceLine product={selected} />
            <p className="text-base text-fg-2" lang={locale}>{locale === 'id' ? selected.description_id : selected.description_en}</p>
            <span className="kk-pill self-start" data-tone={selected.availability === 'out of stock' ? 'fallback' : 'pass'}>{selected.availability === 'out of stock' ? t('preview.soldOut') : t('preview.available')}</span>
            <ChatCta product={selected} waNumber={waNumber} />
          </div>
        </BottomSheet>
      ) : null}
    </>
  );
}
