import { Link, useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Column } from '../components/Column';
import { EmptyState } from '../components/EmptyState';
import { AlertIcon, ArrowRightIcon, CheckIcon, PhotoIcon, RetryIcon } from '../components/Icons';
import { Skeleton } from '../components/Skeleton';
import { StageStrip, type TileState } from '../components/StageStrip';
import { StickyBar } from '../components/StickyBar';
import { ApiError, retryCleanup, useJob, type JobState } from '../lib/api';
import { photoNumber, STAGE_LETTERS } from '../lib/display';
import { STAGE_ORDER, sortedErrors, stageIndex, tileStates, type StageError } from '../lib/jobView';
import { useT } from '../lib/locale';
import { isGoldenJobId } from '../lib/golden';
import { setStoredJobId } from '../lib/store';

/** Products on `photoIndex` that fell back to the composite: the ones a cleanup retry can fix. */
function retryTargets(job: JobState, photoIndex: number): string[] {
  return job.products
    .filter((product) => product.source_photo_index === photoIndex && product.cleanup_method === 'composite')
    .map((product) => product.sku);
}

/** Determinate bar fed by `JobState.progress`. Width is a transform, so it never triggers layout. */
function Meter({ done, total, finished, label }: { done: number; total: number; finished: boolean; label: string }) {
  const ratio = finished ? 1 : total > 0 ? Math.min(1, done / total) : 0;
  return (
    <div className="flex flex-col gap-1">
      <p className="text-sm font-bold text-fg-2">{label}</p>
      <div
        className="kk-meter"
        data-state={finished ? 'done' : 'running'}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={Math.max(total, 1)}
        aria-valuenow={Math.min(done, Math.max(total, 1))}
      >
        <span style={{ transform: `scaleX(${ratio})` }} />
      </div>
    </div>
  );
}

/** Screen 2: poll the job, show the A–E strip, list per-photo errors with a retry each. */
export function ProgressScreen({ jobId }: { jobId: string }) {
  const { t, errorText } = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const query = useJob(jobId);
  const [retrying, setRetrying] = useState<ReadonlySet<string>>(new Set());
  const [retryFailure, setRetryFailure] = useState<string | null>(null);

  const job = query.data;

  async function retry(error: StageError, skus: readonly string[]) {
    const key = `${error.photo_index}:${error.stage}`;
    setRetrying((current) => new Set(current).add(key));
    setRetryFailure(null);
    try {
      await Promise.all(skus.map((sku) => retryCleanup(jobId, sku)));
    } catch (failure) {
      const code = failure instanceof ApiError ? failure.code : null;
      setRetryFailure(errorText(code ?? 'internal_error') ?? t('errors.internal_error'));
    } finally {
      setRetrying((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
      await queryClient.invalidateQueries({ queryKey: ['job', jobId] });
    }
  }

  function uploadAgain() {
    setStoredJobId(null);
    void navigate({ to: '/' });
  }

  if (query.isError && job === undefined) {
    const code = query.error.code;
    return (
      <Column>
        <h1 className="text-2xl">{t('progress.title')}</h1>
        <div className="mt-6">
          <EmptyState text={t('progress.loadFailed')} icon={<AlertIcon size={24} />}>
            {code !== null ? <p className="text-sm text-danger">{errorText(code)}</p> : null}
            <button type="button" className="kk-btn kk-btn-secondary kk-tactile" onClick={uploadAgain}>
              {t('progress.uploadAgain')}
            </button>
          </EmptyState>
        </div>
      </Column>
    );
  }

  if (job === undefined) {
    return (
      <Column>
        <h1 className="text-2xl">{t('progress.title')}</h1>
        <div className="mt-6 flex flex-col gap-3" role="status" aria-label={t('common.loading')}>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-5 w-1/2" />
        </div>
      </Column>
    );
  }

  const states = tileStates(job);
  const stageNames = STAGE_ORDER.map((stage) => t(`stages.${stage}`));
  const stateNames: Record<TileState, string> = {
    done: t('progress.stateDone'),
    active: t('progress.stateActive'),
    pending: t('progress.statePending'),
    failed: t('progress.stateFailed'),
  };

  const errors = sortedErrors(job.errors);
  const productCount = job.products.length;
  const running = job.status === 'queued' || job.status === 'running';
  const finished = job.status === 'done';
  const index = stageIndex(job.stage);
  const caption =
    job.status === 'queued'
      ? t('progress.queued')
      : running
        ? t('progress.running', { letter: STAGE_LETTERS[index] ?? '', stage: stageNames[index] ?? '' })
        : '';
  const title =
    finished
      ? errors.length > 0
        ? t('progress.titleDoneWithNotes')
        : t('progress.titleDone')
      : job.status === 'failed'
        ? t('progress.titleFailed')
        : t('progress.title');
  const clean = finished && errors.length === 0 && productCount > 0;

  return (
    <>
      <Column>
        <h1 className="text-2xl">{title}</h1>
        {finished && errors.length > 0 && productCount > 0 ? (
          <p className="mt-2 text-base text-fg-2">{t('progress.doneWithNotes', { n: productCount })}</p>
        ) : null}

        {isGoldenJobId(jobId) ? (
          <p className="kk-card-flat mt-3 px-3 py-2 text-sm text-fg-2" role="note">
            {t('notice.mock')}
          </p>
        ) : null}

        <section className="kk-card mt-4 flex flex-col gap-4 p-4" aria-label={t('progress.stripLabel')}>
          <StageStrip
            label={t('progress.stripLabel')}
            stageNames={stageNames}
            stateNames={stateNames}
            states={states}
          />
          {caption !== '' ? (
            <p className="text-sm font-bold text-fg-2" aria-live="polite">
              {caption}
            </p>
          ) : null}

          {clean ? (
            <div className="flex items-center gap-3">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-success text-surface">
                <CheckIcon size={24} />
              </span>
              <div className="min-w-0">
                <p className="font-display text-lg font-bold">
                  {t('review.count', { n: productCount })}
                </p>
                <p className="text-sm text-fg-2">
                  {t('progress.photos', { done: job.progress.photos_done, total: job.progress.photos_total })}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <Meter
                done={job.progress.photos_done}
                total={job.progress.photos_total}
                finished={finished}
                label={t('progress.photos', { done: job.progress.photos_done, total: job.progress.photos_total })}
              />
              <Meter
                done={job.progress.products_cleaned}
                total={job.progress.products_total}
                finished={finished}
                label={t('progress.products', {
                  done: job.progress.products_cleaned,
                  total: job.progress.products_total,
                })}
              />
            </div>
          )}
        </section>

        {errors.length > 0 ? (
          <section className="mt-6" aria-labelledby="errors-title">
            <h2 id="errors-title" className="text-xl">
              {t('progress.errorsTitle')}
            </h2>
            <ul className="m-0 mt-3 flex list-none flex-col gap-3 p-0">
              {errors.map((error) => {
                const key = `${error.photo_index}:${error.stage}`;
                const skus = error.stage === 'cleanup' ? retryTargets(job, error.photo_index) : [];
                const busy = retrying.has(key);
                return (
                  <li key={key} className="kk-card-flat m-0 p-3">
                    <p className="flex items-center gap-2 text-sm font-bold text-fg">
                      <span className="text-danger">
                        <AlertIcon size={16} />
                      </span>
                      {t('progress.errorItem', {
                        n: photoNumber(error.photo_index),
                        stage: t(`stages.${error.stage}`),
                      })}
                    </p>
                    <p className="mt-1 text-sm text-fg-2">{errorText(error.code)}</p>
                    {skus.length > 0 ? (
                      <button
                        type="button"
                        className="kk-btn kk-btn-secondary kk-tactile mt-3"
                        disabled={busy}
                        onClick={() => void retry(error, skus)}
                      >
                        <RetryIcon size={16} />
                        {busy ? t('progress.retrying') : t('progress.retryItem', { n: photoNumber(error.photo_index) })}
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {retryFailure !== null ? (
              <p role="alert" className="mt-3 text-sm text-danger">
                {retryFailure}
              </p>
            ) : null}
          </section>
        ) : null}

        {!running && productCount === 0 ? (
          <div className="mt-6">
            <EmptyState text={t('progress.noProducts')} icon={<PhotoIcon size={24} />}>
              <button type="button" className="kk-btn kk-btn-secondary kk-tactile" onClick={uploadAgain}>
                {t('progress.uploadAgain')}
              </button>
            </EmptyState>
          </div>
        ) : null}

        {!running && productCount > 0 ? (
          <div className="mt-4 flex justify-center">
            <button type="button" className="kk-btn kk-btn-ghost" onClick={uploadAgain}>
              {t('progress.uploadAgain')}
            </button>
          </div>
        ) : null}
      </Column>

      {!running && productCount > 0 ? (
        <StickyBar>
          <Link to="/review" className="kk-btn kk-btn-primary kk-tactile w-full">
            {finished
              ? t('progress.reviewCta', { n: productCount })
              : t('progress.reviewPartial', { n: productCount })}
            <ArrowRightIcon size={20} />
          </Link>
        </StickyBar>
      ) : null}
    </>
  );
}
