import { useNavigate, Link } from '@tanstack/react-router';
import { useEffect, useMemo } from 'react';

import { Column } from '../components/Column';
import { AlertIcon, ArrowRightIcon, CheckIcon, LayersIcon, PhotoIcon, StoreIcon } from '../components/Icons';
import { EmptyState } from '../components/EmptyState';
import { ReviewCard } from '../components/ReviewCard';
import { Skeleton } from '../components/Skeleton';
import { StickyBar } from '../components/StickyBar';
import { isJobActive, useJob } from '../lib/api';
import {
  canExport,
  hydrateCatalog,
  readiness,
  syncServerFields,
  type ReviewedProduct,
} from '../lib/catalog';
import { useT } from '../lib/locale';
import { replaceCatalog, setStoredJobId, useCatalog, useStoredJobId } from '../lib/store';

/** At most this many SKUs are named inline in the bar; the rest are counted. */
const NAMED_BLOCKERS = 3;

/** Scrolls to the first product that blocks export and puts the cursor in its price field. */
function focusFirstBlocker(blockers: readonly ReviewedProduct[]): void {
  const first = blockers[0];
  if (first === undefined) return;
  const card = document.getElementById(`card-${first.sku}`);
  card?.scrollIntoView({ block: 'center' });
  card?.querySelector<HTMLInputElement>('input[inputmode="numeric"]')?.focus({ preventScroll: true });
}

/** Screen 3: review and price every product. The client is the source of truth from here on. */
export function ReviewScreen() {
  const { t } = useT();
  const navigate = useNavigate();
  const catalog = useCatalog();
  const jobId = useStoredJobId();
  const query = useJob(jobId);
  const job = query.data;

  // The stored catalog is current unless a different job has been started since it was built.
  const catalogIsCurrent =
    catalog !== null && catalog.length > 0 && (jobId === null || catalog[0]?.job_id === jobId);

  // Hydrate once the job has settled; afterwards only refresh the photo and gate the server owns.
  useEffect(() => {
    if (job === undefined || isJobActive(job) || job.job_id !== jobId) return;
    if (!catalogIsCurrent) {
      if (job.products.length > 0) replaceCatalog(hydrateCatalog(job.products));
      return;
    }
    const synced = syncServerFields(catalog ?? [], job.products);
    if (synced !== null) replaceCatalog(synced);
  }, [job, jobId, catalog, catalogIsCurrent]);

  // The AI's guess is helper text only: read from the live job, held in memory, never stored.
  const suggestions = useMemo(() => {
    const bySku = new Map<string, number | null>();
    if (job !== undefined && catalog !== null && job.job_id === catalog[0]?.job_id) {
      for (const product of job.products) bySku.set(product.sku, product.price_suggestion_idr);
    }
    return bySku;
  }, [job, catalog]);

  function uploadNew() {
    setStoredJobId(null);
    void navigate({ to: '/' });
  }

  if (!catalogIsCurrent || catalog === null) {
    const running = job !== undefined && isJobActive(job);
    return (
      <Column>
        <h1 className="text-2xl">{t('review.title')}</h1>
        <div className="mt-5">
          {jobId !== null && job === undefined && !query.isError ? (
            <div className="flex flex-col gap-3" role="status" aria-label={t('common.loading')}>
              <Skeleton className="h-36 w-full" />
              <Skeleton className="h-36 w-full" />
            </div>
          ) : running ? (
            <EmptyState text={t('review.stillRunning')} icon={<LayersIcon size={24} />}>
              <Link to="/jobs/$jobId" params={{ jobId: jobId ?? '' }} className="kk-btn kk-btn-secondary kk-tactile">
                {t('review.seeProgress')}
              </Link>
            </EmptyState>
          ) : (
            <EmptyState
              text={query.isError ? t('review.loadFailed') : t('review.empty')}
              icon={query.isError ? <AlertIcon size={24} /> : <PhotoIcon size={24} />}
            >
              <button type="button" className="kk-btn kk-btn-secondary kk-tactile" onClick={uploadNew}>
                {t('review.uploadNew')}
              </button>
            </EmptyState>
          )}
        </div>
      </Column>
    );
  }

  const state = readiness(catalog);
  const exportable = canExport(state);
  const named = state.blockers.slice(0, NAMED_BLOCKERS).map((product) => product.sku).join(', ');
  const blockerText =
    state.blockers.length > NAMED_BLOCKERS
      ? t('review.blockedMore', { list: named, n: state.blockers.length - NAMED_BLOCKERS })
      : t('review.blockedList', { list: named });

  return (
    <>
      <Column>
        <h1 className="text-2xl">{t('review.title')}</h1>
        <p className="mt-2 text-base text-fg-2">{t('review.lead')}</p>

        <div className="mt-3 flex items-center justify-between gap-2">
          <span className="kk-count whitespace-nowrap">{t('review.count', { n: catalog.length })}</span>
          <div className="flex items-center">
            <Link to="/review/preview" className="kk-btn kk-btn-ghost px-2 whitespace-nowrap text-accent-2">
              <StoreIcon size={16} />
              {t('review.previewCta')}
            </Link>
            <button type="button" className="kk-btn kk-btn-ghost px-2 whitespace-nowrap text-fg-2" onClick={uploadNew}>
              {t('review.uploadNew')}
            </button>
          </div>
        </div>
        {state.excluded > 0 ? (
          <p className="mt-2 text-sm text-fg-muted">{t('review.excluded', { n: state.excluded })}</p>
        ) : null}

        <ul className="m-0 mt-3 flex list-none flex-col gap-3 p-0">
          {catalog.map((product) => (
            <ReviewCard key={product.sku} product={product} suggestion={suggestions.get(product.sku) ?? null} />
          ))}
        </ul>
      </Column>

      <StickyBar>
        <p
          id="review-summary"
          className="kk-count mx-auto"
          data-tone={exportable ? 'ready' : 'warning'}
          aria-live="polite"
        >
          {exportable ? <CheckIcon size={16} /> : <AlertIcon size={16} />}
          {t('review.summary', { n: state.ready, m: state.blockers.length })}
        </p>
        {state.included === 0 ? (
          <p className="text-center text-sm text-fg-2">{t('review.noneIncluded')}</p>
        ) : state.blockers.length > 0 ? (
          <p className="text-center text-sm font-bold text-warning" aria-live="polite">
            {blockerText}
          </p>
        ) : null}
        <button
          type="button"
          className="kk-btn kk-btn-primary kk-tactile w-full"
          aria-disabled={!exportable}
          aria-describedby="review-summary"
          onClick={() => {
            if (exportable) void navigate({ to: '/export' });
            else focusFirstBlocker(state.blockers);
          }}
        >
          {t('review.exportCta')}
          {exportable ? <ArrowRightIcon size={20} /> : null}
        </button>
      </StickyBar>
    </>
  );
}
