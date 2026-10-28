import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Column } from '../components/Column';
import { Switch } from '../components/Switch';
import { StickyBar } from '../components/StickyBar';
import { Toast } from '../components/Toast';
import { CheckIcon, CloseIcon, PhotoIcon, PlusIcon, UploadIcon } from '../components/Icons';
import { ApiError, BackendUnavailableError, createJob, fetchSamplePack, type SamplePack } from '../lib/api';
import { isGoldenJobId } from '../lib/golden';
import { useT, type MessageKey } from '../lib/locale';
import {
  getDemoSafe,
  replaceCatalog,
  setDemoSafe,
  setStoredJobId,
  useDemoSafe,
  useStoredJobId,
} from '../lib/store';

interface PickedPhoto {
  id: number;
  file: File;
  caption: string;
}

const LIVE_PACKS: ReadonlyArray<{ pack: SamplePack; label: MessageKey }> = [
  { pack: 'a', label: 'samples.packA' },
  { pack: 'b', label: 'samples.packB' },
  { pack: 'c', label: 'samples.packC' },
];

/** Demo Safe Mode only: pack d is a golden bundle with no live pipeline behind it (merge demo). */
const SAFE_ONLY_PACK: { pack: SamplePack; label: MessageKey } = { pack: 'd', label: 'samples.packD' };

const SAFE_PACKS: ReadonlyArray<{ pack: SamplePack; label: MessageKey }> = [...LIVE_PACKS, SAFE_ONLY_PACK];

function Thumb({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  return (
    <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-[12px] bg-subtle text-fg-muted">
      {url === null ? (
        <PhotoIcon />
      ) : (
        <img src={url} alt="" className="h-full w-full object-cover" />
      )}
    </span>
  );
}

/** Screen 1: pick photos (or a sample pack), add captions, start the job. */
export function UploadScreen() {
  const { t, errorText } = useT();
  const navigate = useNavigate();
  const demoSafe = useDemoSafe();
  const storedJobId = useStoredJobId();

  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [general, setGeneral] = useState('');
  const [pack, setPack] = useState<SamplePack | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [backendMissing, setBackendMissing] = useState(false);
  const nextId = useRef(1);
  const dismissToast = useCallback(() => setToast(null), []);

  // Pack d exists only in Demo Safe Mode. Turning the toggle off (here or in another tab) drops it,
  // so a live run can never start from a golden-only pack.
  useEffect(() => {
    if (!demoSafe && pack === SAFE_ONLY_PACK.pack) setPack(null);
  }, [demoSafe, pack]);

  const packs = demoSafe ? SAFE_PACKS : LIVE_PACKS;
  const canStart = !busy && (pack !== null || photos.length > 0);

  function addFiles(list: FileList | null) {
    if (list === null) return;
    const added: PickedPhoto[] = Array.from(list)
      .filter((file) => file.type.startsWith('image/'))
      .map((file) => ({ id: nextId.current++, file, caption: '' }));
    if (added.length > 0) setPhotos((current) => [...current, ...added]);
  }

  function setCaption(id: number, caption: string) {
    setPhotos((current) => current.map((photo) => (photo.id === id ? { ...photo, caption } : photo)));
  }

  function removePhoto(id: number) {
    setPhotos((current) => current.filter((photo) => photo.id !== id));
  }

  async function start() {
    if (!canStart) return;
    setBusy(true);
    setBackendMissing(false);
    try {
      const live = pack !== null && !getDemoSafe();
      if (pack === SAFE_ONLY_PACK.pack && live) throw new Error('pack d has no live path');
      const sample = live ? await fetchSamplePack(pack) : null;
      const jobId = await createJob({
        photos: sample === null ? photos.map((photo) => photo.file) : sample.photos,
        photoCaptions: sample === null ? photos.map((photo) => photo.caption) : sample.photos.map(() => ''),
        generalCaption: sample === null ? general : sample.captions,
        demoSafe: getDemoSafe(),
        samplePack: pack,
      });
      // A golden run always has the same id: starting it again must start from the bundled catalog.
      if (isGoldenJobId(jobId)) replaceCatalog([]);
      setStoredJobId(jobId);
      await navigate({ to: '/jobs/$jobId', params: { jobId } });
    } catch (error) {
      if (error instanceof BackendUnavailableError) {
        setBackendMissing(true);
        setBusy(false);
        return;
      }
      const code = error instanceof ApiError ? error.code : null;
      const offline = error instanceof ApiError && error.status === 0;
      setToast(
        offline
          ? t('upload.networkFailed')
          : (errorText(code ?? 'internal_error') ?? t('errors.internal_error')),
      );
      setBusy(false);
    }
  }

  return (
    <>
      <Column>
        <h1 className="max-w-[18ch] text-2xl">{t('upload.title')}</h1>
        <p className="mt-2 text-base text-fg-2">{t('upload.lead')}</p>

        {storedJobId !== null ? (
          <section
            className="kk-card-flat mt-4 flex items-center gap-2 py-2 pr-2 pl-4"
            aria-labelledby="resume-title"
          >
            <div className="min-w-0 flex-1">
              <h2 id="resume-title" className="text-sm font-bold">
                {t('upload.resumeTitle')}
              </h2>
              <p className="sr-only">{t('upload.resumeHint')}</p>
            </div>
            <button
              type="button"
              className="kk-btn kk-btn-secondary kk-btn-compact kk-tactile"
              onClick={() => void navigate({ to: '/jobs/$jobId', params: { jobId: storedJobId } })}
            >
              {t('upload.resumeOpen')}
            </button>
            <button type="button" className="kk-btn kk-btn-ghost px-2" onClick={() => setStoredJobId(null)}>
              {t('upload.resumeDismiss')}
            </button>
          </section>
        ) : null}

        <label className="kk-hero kk-card-tap kk-tactile mt-4" data-filled={photos.length > 0}>
          <span className="kk-icon-tile">
            {photos.length > 0 ? <PlusIcon size={20} /> : <UploadIcon size={20} />}
          </span>
          <span className="min-w-0">
            <span className="block font-display text-lg font-bold text-fg">
              {photos.length > 0 ? t('upload.addMore') : t('upload.dropTitle')}
            </span>
            <span className="block text-sm text-fg-2">{t('upload.dropHint')}</span>
          </span>
          <input
            type="file"
            accept="image/*"
            multiple
            className="sr-only"
            onChange={(event) => {
              addFiles(event.target.files);
              event.target.value = '';
            }}
          />
        </label>

        {photos.length > 0 ? (
          <section className="mt-5" aria-labelledby="photos-title">
            <div className="flex items-center justify-between gap-2">
              <h2 id="photos-title" className="text-xl">
                {t('upload.photosHeading')}
              </h2>
              <span className="kk-count" aria-live="polite" data-testid="photo-count">
                <PhotoIcon size={16} />
                {t('upload.photosCount', { n: photos.length })}
              </span>
            </div>
            <ul className="m-0 mt-3 flex list-none flex-col gap-3 p-0">
              {photos.map((photo, index) => (
                <li key={photo.id} className="kk-card-flat m-0 p-3">
                  <div className="flex items-center gap-3">
                    <Thumb file={photo.file} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-bold">{t('upload.photoLabel', { n: index + 1 })}</p>
                      <p className="truncate text-xs text-fg-muted">{photo.file.name}</p>
                    </div>
                    <button
                      type="button"
                      className="kk-btn kk-btn-quiet"
                      aria-label={t('upload.removePhoto', { n: index + 1 })}
                      onClick={() => removePhoto(photo.id)}
                    >
                      <CloseIcon size={16} />
                    </button>
                  </div>
                  <input
                    type="text"
                    className="kk-input mt-3"
                    value={photo.caption}
                    aria-label={t('upload.captionPhoto', { n: index + 1 })}
                    placeholder={t('upload.captionPhotoPlaceholder')}
                    onChange={(event) => setCaption(photo.id, event.target.value)}
                  />
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section className="mt-5" aria-labelledby="samples-title" aria-describedby="samples-hint">
          <h2 id="samples-title" className="text-xl">
            {t('samples.title')}
          </h2>
          <p id="samples-hint" className="sr-only">
            {t('samples.hint')}
          </p>
          <div className={`mt-3 grid gap-2 ${demoSafe ? 'grid-cols-2' : 'grid-cols-3'}`}>
            {packs.map((entry) => {
              const selected = pack === entry.pack;
              return (
                <button
                  key={entry.pack}
                  type="button"
                  data-sample={entry.pack}
                  className="kk-pack kk-tactile"
                  aria-pressed={selected}
                  onClick={() => setPack(selected ? null : entry.pack)}
                >
                  <span className="kk-pack-tile" aria-hidden="true">
                    {entry.pack.toUpperCase()}
                  </span>
                  <span className="text-sm font-bold text-fg">{t(entry.label)}</span>
                  {selected ? <span className="sr-only">{t('samples.chosen')}</span> : null}
                  <span className="kk-pack-check" aria-hidden="true">
                    <CheckIcon size={16} />
                  </span>
                </button>
              );
            })}
          </div>
          {pack !== null ? (
            <p className="mt-2 text-sm text-fg-2">{t('samples.selected', { pack: pack.toUpperCase() })}</p>
          ) : null}
        </section>

        <details className="kk-details kk-card-flat mt-4 px-4">
          <summary>{t('upload.captionGeneral')}</summary>
          <div className="pb-3">
            <input
              id="general-caption"
              type="text"
              className="kk-input"
              value={general}
              aria-label={t('upload.captionGeneral')}
              placeholder={t('upload.captionGeneralPlaceholder')}
              onChange={(event) => setGeneral(event.target.value)}
            />
            <p className="mt-2 text-sm text-fg-muted">{t('upload.captionHelp')}</p>
          </div>
        </details>

        <section className="kk-card-flat mt-4 py-1 pr-2 pl-4">
          <div className="flex min-h-[44px] items-center gap-3">
            <h2 id="demo-title" className="min-w-0 flex-1 text-base font-bold">
              {t('demo.title')}
            </h2>
            <Switch checked={demoSafe} onChange={setDemoSafe} labelledBy="demo-title" describedBy="demo-help" />
          </div>
          <p
            id="demo-help"
            className="pr-2 pb-3 text-xs font-medium text-fg-2 [@media(max-height:830px)]:sr-only"
          >
            {t('demo.help')}
          </p>
        </section>

        {toast !== null ? <Toast text={toast} onDismiss={dismissToast} /> : null}
      </Column>

      <StickyBar>
        {backendMissing ? (
          <p className="kk-card-flat px-3 py-2 text-sm text-fg-2" role="alert">
            {t('upload.backendUnavailable')}
          </p>
        ) : null}
        <button
          type="button"
          className="kk-btn kk-btn-primary kk-tactile w-full"
          disabled={!canStart}
          onClick={() => void start()}
        >
          {busy ? t('upload.ctaBusy') : t('upload.cta')}
        </button>
        {!canStart && !busy ? <p className="text-center text-sm text-fg-muted">{t('upload.ctaNeed')}</p> : null}
        {photos.length > 0 && canStart ? (
          <p className="text-center text-sm font-bold">{t('upload.photosCount', { n: photos.length })}</p>
        ) : null}
      </StickyBar>
    </>
  );
}
