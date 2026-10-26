import { Link, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';

import { AlertIcon, DownloadIcon, PhotoIcon, SheetIcon, TextIcon, ChatIcon } from '../components/Icons';
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

function Column({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-[420px] px-4 pt-6 pb-6">{children}</div>;
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
        <h1 className="text-2xl">{t('export.title')}</h1>
        <div className="mt-5">
          <EmptyState text={t('export.empty')}>
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
        <h1 className="text-2xl">{t('export.title')}</h1>
        <p className="mt-2 text-base text-fg-2">{t('export.lead')}</p>
        {scope.mixed ? <p className="mt-2 text-sm text-warning">{t('export.mixedJobs')}</p> : null}

        <section className="mt-5 flex flex-col gap-4" aria-labelledby="export-seller">
          <h2 id="export-seller" className="text-lg">
            {t('export.sellerTitle')}
          </h2>
          <div>
            <label htmlFor="export-store" className="text-sm font-bold">
              {t('export.storeNameLabel')}
            </label>
            <input
              id="export-store"
              type="text"
              className="kk-input mt-2"
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
              className="kk-input kk-money mt-2"
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
          <div className="kk-card-flat mt-5 flex flex-col gap-2 p-4" role="alert">
            <p className="text-sm font-bold text-warning">{t('export.blockedIntro')}</p>
            {namedBlockers.length > 0 ? (
              <p className="kk-money text-sm break-words text-fg-2">{namedBlockers.join(', ')}</p>
            ) : null}
            <Link to="/review" className="kk-btn kk-btn-secondary kk-tactile mt-1">
              {t('export.backToReview')}
            </Link>
          </div>
        ) : null}

        {state.included === 0 ? <p className="mt-5 text-sm text-fg-2">{t('review.noneIncluded')}</p> : null}

        {failureText !== null && failure?.kind !== undefined && !(failure.kind === 'code' && failure.code === 'price_missing') ? (
          <div className="kk-card-flat mt-5 flex flex-col gap-3 p-4" role="alert">
            <p className="flex items-start gap-2 text-sm font-bold text-danger">
              <AlertIcon size={18} />
              <span className="min-w-0 flex-1 break-words">{failureText}</span>
            </p>
            <button type="button" className="kk-btn kk-btn-secondary kk-tactile" disabled={busy} onClick={() => void submit()}>
              {t('common.retry')}
            </button>
          </div>
        ) : null}

        <section className="mt-6 flex flex-col gap-3" aria-labelledby="export-files">
          <h2 id="export-files" className="text-lg">
            {t('export.downloadsTitle')}
          </h2>
          <p className="text-sm text-fg-2" role="status" aria-live="polite">
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
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {KINDS.map(({ kind, label, desc, Icon }) => {
              const file = filesByKind.get(kind);
              return (
                <li key={kind} className="kk-card m-0 flex flex-col gap-3 p-3">
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 text-fg-2">
                      <Icon size={24} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="font-display text-lg break-words">{t(label)}</p>
                      <p className="text-sm text-fg-2">{t(desc)}</p>
                      {file !== undefined ? (
                        <p className="kk-money mt-1 text-xs break-all text-fg-muted">{file.filename}</p>
                      ) : null}
                    </div>
                  </div>
                  {file !== undefined ? (
                    <a
                      href={file.url}
                      download={file.filename}
                      className="kk-btn kk-btn-secondary kk-tactile w-full"
                      aria-label={`${t('export.download')}: ${t(label)}`}
                    >
                      <DownloadIcon size={18} />
                      {t('export.download')}
                    </a>
                  ) : (
                    <button type="button" className="kk-btn kk-btn-secondary w-full" disabled aria-label={`${t('export.download')}: ${t(label)}`}>
                      <DownloadIcon size={18} />
                      {t('export.download')}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        <details className="kk-details kk-card-flat mt-6 px-4 py-1">
          <summary>{t('export.guide.title')}</summary>
          <div className="flex flex-col gap-3 pt-1 pb-3 text-sm text-fg-2">
            <ol className="m-0 flex list-decimal flex-col gap-1.5 pl-5">
              <li>{t('export.guide.step1')}</li>
              <li>{t('export.guide.step2')}</li>
              <li>{t('export.guide.step3')}</li>
              <li>{t('export.guide.step4')}</li>
            </ol>
            <p>{t('export.guide.noteImages')}</p>
            <p>{t('export.guide.noteChat')}</p>
          </div>
        </details>

        <Link to="/review" className="kk-btn kk-btn-secondary kk-tactile mt-6 w-full">
          {t('export.backToReview')}
        </Link>
      </Column>

      <StickyBar>
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
