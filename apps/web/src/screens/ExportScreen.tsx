import { Link, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';

import { Column } from '../components/Column';
import { AlertIcon, ArrowRightIcon, CheckIcon, DownloadIcon, PhotoIcon, SheetIcon, TextIcon, ChatIcon, UploadIcon } from '../components/Icons';
import { EmptyState } from '../components/EmptyState';
import { StickyBar } from '../components/StickyBar';
import { ApiError, exportJob, type ExportKind, type ExportResponse } from '../lib/api';
import { canExport, exportScope, isWaNumber, readiness } from '../lib/catalog';
import { useT, type MessageKey } from '../lib/locale';
import {
  setStoredJobId,
  setStoreName,
  setWaNumber,
  useCatalog,
  useSeller,
  useStoredJobId,
} from '../lib/store';

const KINDS: ReadonlyArray<{
  kind: ExportKind;
  label: MessageKey;
  desc: MessageKey;
  Icon: typeof SheetIcon;
}> = [
  { kind: 'catalog_csv', label: 'export.kind.catalog_csv.label', desc: 'export.kind.catalog_csv.desc', Icon: SheetIcon },
  { kind: 'images_zip', label: 'export.kind.images_zip.label', desc: 'export.kind.images_zip.desc', Icon: PhotoIcon },
  {
    kind: 'captions_instagram',
    label: 'export.kind.captions_instagram.label',
    desc: 'export.kind.captions_instagram.desc',
    Icon: TextIcon,
  },
  {
    kind: 'price_list_whatsapp',
    label: 'export.kind.price_list_whatsapp.label',
    desc: 'export.kind.price_list_whatsapp.desc',
    Icon: ChatIcon,
  },
];

/** Longest store name the field accepts (same limit as the preview screen). */
const STORE_NAME_MAX = 60;

/** At most this many blocking SKUs are listed by name; the rest are counted by the review page. */
const NAMED_BLOCKERS = 8;

type Failure = { kind: 'code'; code: string | null } | { kind: 'network' };

interface ExportDone {
  response: ExportResponse;
  /** The request body as sent, to notice when the inputs have changed since. */
  signature: string;
}

/** Screen 5: seller details, one export request, four downloads, and how to use the CSV. */
export function ExportScreen() {
  const { t, errorText } = useT();
  const navigate = useNavigate();
  const catalog = useCatalog();
  const seller = useSeller();
  const currentJobId = useStoredJobId();

  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<ExportDone | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [touched, setTouched] = useState({ name: false, wa: false });

  const scope = exportScope(catalog, currentJobId);

  if (scope === null) {
    return (
      <Column>
        <div className="mt-5">
          <EmptyState text={t('export.empty')} icon={<UploadIcon size={24} />}>
            <button
              type="button"
              className="kk-btn kk-btn-secondary kk-tactile"
              onClick={() => {
                setStoredJobId(null);
                void navigate({ to: '/' });
              }}
            >
              {t('export.emptyCta')}
            </button>
          </EmptyState>
        </div>
      </Column>
    );
  }

  const storeName = seller?.store_name ?? '';
  const waNumber = seller?.wa_number ?? '';
  const nameError = storeName.trim() === '';
  const waError = waNumber === '' ? 'empty' : isWaNumber(waNumber) ? null : 'format';
  const sellerValid = !nameError && waError === null;

  const state = readiness(scope.products);
  const exportable = canExport(state);
  const ready = exportable && sellerValid;
  const viaChat = scope.products.filter((product) => product.included && product.price_via_chat).length;

  const signature = JSON.stringify([scope.products, storeName.trim(), waNumber]);
  const stale = done !== null && done.signature !== signature;

  async function submit() {
    setTouched({ name: true, wa: true });
    if (busy || !ready || scope === null) return;
    setBusy(true);
    setFailure(null);
    const body = {
      products: scope.products,
      seller: { store_name: storeName.trim(), wa_number: waNumber },
    };
    try {
      const response = await exportJob(scope.jobId, body);
      setDone({ response, signature });
    } catch (error) {
      if (error instanceof ApiError) {
        setFailure(error.status === 0 ? { kind: 'network' } : { kind: 'code', code: error.code });
      } else {
        setFailure({ kind: 'code', code: null });
      }
    } finally {
      setBusy(false);
    }
  }

  const failureText =
    failure === null
      ? null
      : failure.kind === 'network'
        ? t('export.networkError')
        : ((failure.code === null ? null : errorText(failure.code)) ?? t('export.failed'));
  // The server's price backstop: name the SKUs this client can see as unpriced, if any.
  const showBlockers = state.blockers.length > 0 || (failure?.kind === 'code' && failure.code === 'price_missing');
  const namedBlockers = state.blockers.slice(0, NAMED_BLOCKERS).map((product) => product.sku);

  const filesByKind = new Map(done?.response.files.map((file) => [file.kind, file]) ?? []);

  return (
    <>
      <Column>
        {scope.mixed ? <p className="mt-2 text-sm text-warning">{t('export.mixedJobs')}</p> : null}

        <section className="mt-3 flex flex-col gap-3" aria-labelledby="export-seller">
          <h2 id="export-seller" className="text-sm font-bold text-fg-2">
            {t('export.sellerTitle')}
          </h2>
          <div>
            <label htmlFor="export-store" className="text-sm font-bold">
              {t('export.storeNameLabel')}
            </label>
            <input
              id="export-store"
              type="text"
              className="kk-input mt-1"
              maxLength={STORE_NAME_MAX}
              autoComplete="organization"
              value={storeName}
              placeholder={t('export.storeNamePlaceholder')}
              aria-invalid={touched.name && nameError}
              aria-describedby={touched.name && nameError ? 'export-store-error' : undefined}
              onBlur={() => setTouched((current) => ({ ...current, name: true }))}
              onChange={(event) => setStoreName(event.target.value)}
            />
            {touched.name && nameError ? (
              <p id="export-store-error" className="mt-1 px-0.5 text-sm font-bold text-danger">
                {t('export.storeNameError')}
              </p>
            ) : null}
          </div>
          <div>
            <label htmlFor="export-wa" className="text-sm font-bold">
              {t('export.waLabel')}
            </label>
            <input
              id="export-wa"
              type="text"
              inputMode="numeric"
              autoComplete="tel-national"
              className="kk-input mt-1"
              maxLength={20}
              value={waNumber}
              placeholder={t('export.waPlaceholder')}
              aria-invalid={touched.wa && waError !== null}
              aria-describedby={touched.wa && waError === 'empty' ? 'export-wa-error export-wa-hint' : 'export-wa-hint'}
              onBlur={() => setTouched((current) => ({ ...current, wa: true }))}
              onChange={(event) => setWaNumber(event.target.value.replace(/\D/g, ''))}
            />
            <p
              id="export-wa-hint"
              className={`mt-1 px-0.5 text-sm ${touched.wa && waError === 'format' ? 'font-bold text-danger' : 'text-fg-muted'}`}
            >
              {t('export.waHint')}
            </p>
            {touched.wa && waError === 'empty' ? (
              <p id="export-wa-error" className="mt-1 px-0.5 text-sm font-bold text-danger">
                {t('export.waErrorEmpty')}
              </p>
            ) : null}
          </div>
        </section>

        {showBlockers ? (
          <div className="kk-card-flat mt-4 flex flex-col gap-2 border-warning! p-4" role="alert">
            <p className="text-sm font-bold text-warning">{t('export.blockedIntro')}</p>
            {namedBlockers.length > 0 ? (
              <p className="kk-money text-sm break-words text-fg-2">{namedBlockers.join(', ')}</p>
            ) : null}
            <Link to="/review" className="kk-btn kk-btn-secondary kk-tactile mt-1">
              {t('export.backToReview')}
            </Link>
          </div>
        ) : null}

        {state.included === 0 ? <p className="mt-4 text-base text-fg-2">{t('review.noneIncluded')}</p> : null}

        {failureText !== null && failure?.kind !== undefined && !(failure.kind === 'code' && failure.code === 'price_missing') ? (
          <div className="kk-card-flat mt-4 flex flex-col gap-3 p-4" role="alert">
            <p className="flex items-start gap-2 text-sm font-bold text-danger">
              <AlertIcon size={16} />
              <span className="min-w-0 flex-1 break-words">{failureText}</span>
            </p>
            <button type="button" className="kk-btn kk-btn-secondary kk-tactile" disabled={busy} onClick={() => void submit()}>
              {t('common.retry')}
            </button>
          </div>
        ) : null}

        <section className="mt-5 flex flex-col gap-2" aria-labelledby="export-files">
          <h2 id="export-files" className="text-sm font-bold text-fg-2">
            {t('export.downloadsTitle')}
          </h2>
          <p
            className={done === null ? 'sr-only' : 'flex items-center gap-2 text-sm font-bold text-success'}
            role="status"
            aria-live="polite"
          >
            {done !== null ? <CheckIcon size={16} /> : null}
            {done === null ? t('export.downloadsHint') : t('export.done')}
          </p>
          {done !== null ? (
            <>
              <p className="text-sm font-bold">
                {t('export.summary', { n: state.included, m: viaChat, k: state.excluded })}
              </p>
              {stale ? <p className="text-sm text-warning">{t('export.stale')}</p> : null}
            </>
          ) : null}
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {KINDS.map(({ kind, label, desc, Icon }) => {
              const file = filesByKind.get(kind);
              return (
                <li
                  key={kind}
                  className="kk-card kk-export-row m-0 flex min-h-14 items-center gap-3 p-4"
                >
                  <span
                    className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-(--r-sm) ${
                      file !== undefined ? 'bg-surface text-success' : 'bg-surface text-fg-2'
                    }`}
                  >
                    {file !== undefined ? <CheckIcon size={20} /> : <Icon size={20} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="font-display text-base font-bold break-words">{t(label)}</p>
                    <p className="text-sm text-fg-2">{t(desc)}</p>
                    {file !== undefined ? (
                      <p className="kk-money truncate text-xs text-fg-muted">{file.filename}</p>
                    ) : null}
                  </div>
                  {file !== undefined ? (
                    <a
                      href={file.url}
                      download={file.filename}
                      className="kk-btn kk-btn-success kk-btn-icon kk-tactile shrink-0"
                      aria-label={`${t('export.download')}: ${t(label)}`}
                      title={t('export.download')}
                    >
                      <DownloadIcon size={20} />
                    </a>
                  ) : (
                    <button
                      type="button"
                      className="kk-btn kk-btn-secondary kk-btn-icon shrink-0"
                      disabled
                      aria-label={`${t('export.download')}: ${t(label)}`}
                    >
                      <DownloadIcon size={20} />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        {done !== null ? <details className="kk-details kk-card-flat mt-4 px-4">
          <summary>{t('export.shareTitle')}</summary>
          <ul className="m-0 flex list-none flex-col gap-2 pb-4">
            {scope.products.filter((product) => product.included).map((product) => (
              <li key={product.sku}><a className="flex min-h-11 items-center gap-2 text-sm font-bold text-fg-2" href={`/p/${encodeURIComponent(scope.jobId)}/${encodeURIComponent(product.sku)}`} target="_blank" rel="noopener noreferrer"><span className="shrink-0"><ArrowRightIcon size={16} /></span><span className="min-w-0 break-words">{product.name} · {product.sku}</span></a></li>
            ))}
          </ul>
        </details> : null}

        <details className="kk-details kk-card-flat mt-3 px-4" open>
          <summary>{t('export.guide.title')}</summary>
          <div className="flex flex-col gap-3 py-3 text-base text-fg-2">
            <ol className="m-0 flex list-decimal flex-col gap-2 pl-5">
              <li>{t('export.guide.step1')}</li>
              <li>{t('export.guide.step2')}</li>
              <li>{t('export.guide.step3')}</li>
              <li>{t('export.guide.step4')}</li>
            </ol>
            <p>{t('export.guide.noteImages')}</p>
            <p>{t('export.guide.noteChat')}</p>
          </div>
        </details>

      </Column>

      <StickyBar>
        {!ready && !busy ? <p className="text-center text-sm text-fg-2">{!exportable ? t('export.blockedIntro') : t('export.sellerNeeded')}</p> : null}
        <button
          type="button"
          className="kk-btn kk-btn-primary kk-tactile w-full"
          aria-disabled={!ready || busy}
          onClick={() => void submit()}
        >
          {busy ? t('export.ctaBusy') : done === null ? t('export.cta') : t('export.ctaAgain')}
        </button>
      </StickyBar>
    </>
  );
}
