import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Switch } from '../components/Switch';
import { StickyBar } from '../components/StickyBar';
import { Toast } from '../components/Toast';
import { CheckIcon, CloseIcon, PhotoIcon, PlusIcon } from '../components/Icons';
import { ApiError, BackendUnavailableError, createJob, fetchSamplePack, type SamplePack } from '../lib/api';
import { useT, type MessageKey } from '../lib/locale';
import {
  getDemoSafe,
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

const PACKS: ReadonlyArray<{ pack: SamplePack; label: MessageKey }> = [
  { pack: 'a', label: 'samples.packA' },
  { pack: 'b', label: 'samples.packB' },
  { pack: 'c', label: 'samples.packC' },
];

function Thumb({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  return (
    <span className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-[12px] bg-subtle text-fg-muted">
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
      const sample = live ? await fetchSamplePack(pack) : null;
      const jobId = await createJob({
        photos: sample === null ? photos.map((photo) => photo.file) : sample.photos,
        photoCaptions: sample === null ? photos.map((photo) => photo.caption) : sample.photos.map(() => ''),
        generalCaption: sample === null ? general : sample.captions,
        demoSafe: getDemoSafe(),
        samplePack: pack,
      });
      setStoredJobId(jobId);
      await navigate({ to: '/jobs/$jobId', params: { jobId } });
    } catch (error) {
      if (error instanceof BackendUnavailableError) {
        setBackendMissing(true);
        setBusy(false);
        return;
      }
      const code = error instanceof ApiError ? error.code : null;
      const missingSample = pack !== null && error instanceof ApiError && error.status === 404;
      const offline = error instanceof ApiError && error.status === 0;
      setToast(
        missingSample
          ? t('upload.samplePhotosMissing')
          : offline
            ? t('upload.networkFailed')
            : (errorText(code ?? 'internal_error') ?? t('errors.internal_error')),
      );
      setBusy(false);
    }
  }

  return (
    <>
    <div className="mx-auto w-full max-w-[420px] px-4 pt-6 pb-6">
      <h1 className="text-2xl">{t('upload.title')}</h1>
      <p className="mt-2 text-base text-fg-2">{t('upload.lead')}</p>

      {storedJobId !== null ? (
        <section className="kk-card-flat mt-5 p-4" aria-labelledby="resume-title">
          <h2 id="resume-title" className="text-lg">
            {t('upload.resumeTitle')}
          </h2>
          <p className="mt-1 text-sm text-fg-2">{t('upload.resumeHint')}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="kk-btn kk-btn-secondary kk-tactile"
              onClick={() => void navigate({ to: '/jobs/$jobId', params: { jobId: storedJobId } })}
            >
              {t('upload.resumeOpen')}
            </button>
            <button type="button" className="kk-btn kk-btn-ghost" onClick={() => setStoredJobId(null)}>
              {t('upload.resumeDismiss')}
            </button>
          </div>
        </section>
      ) : null}

      <label className="kk-card kk-card-tap kk-tactile mt-5 flex items-center gap-4 p-4">
        <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[16px] bg-accent text-accent-fg">
          {photos.length > 0 ? <PlusIcon size={26} /> : <PhotoIcon size={26} />}
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
          <div className="flex items-baseline justify-between gap-2">
            <h2 id="photos-title" className="text-lg">
              {t('upload.photosHeading')}
            </h2>
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
                    className="kk-btn kk-btn-ghost"
                    aria-label={t('upload.removePhoto', { n: index + 1 })}
                    onClick={() => removePhoto(photo.id)}
                  >
                    <CloseIcon size={18} />
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

      <div className="mt-5">
        <label htmlFor="general-caption" className="text-sm font-bold">
          {t('upload.captionGeneral')}
        </label>
        <input
          id="general-caption"
          type="text"
          className="kk-input mt-2"
          value={general}
          placeholder={t('upload.captionGeneralPlaceholder')}
          onChange={(event) => setGeneral(event.target.value)}
        />
        <p className="mt-2 text-sm text-fg-muted">{t('upload.captionHelp')}</p>
      </div>

      <section className="mt-6" aria-labelledby="samples-title">
        <h2 id="samples-title" className="text-lg">
          {t('samples.title')}
        </h2>
        <p className="mt-1 text-sm text-fg-2">{t('samples.hint')}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {PACKS.map((entry) => (
            <button
              key={entry.pack}
              type="button"
              className="kk-chip kk-tactile"
              aria-pressed={pack === entry.pack}
              onClick={() => setPack(pack === entry.pack ? null : entry.pack)}
            >
              {pack === entry.pack ? <CheckIcon size={16} /> : null}
              {t(entry.label)}
            </button>
          ))}
        </div>
        {pack !== null ? (
          <p className="mt-2 text-sm text-fg-2">{t('samples.selected', { pack: pack.toUpperCase() })}</p>
        ) : null}
      </section>

      <section className="kk-card-flat mt-6 flex items-center gap-3 py-3 pr-2 pl-4">
        <div className="min-w-0 flex-1">
          <h2 id="demo-title" className="text-base font-bold">
            {t('demo.title')}
          </h2>
          <p id="demo-help" className="mt-1 text-sm text-fg-2">
            {t('demo.help')}
          </p>
        </div>
        <Switch checked={demoSafe} onChange={setDemoSafe} labelledBy="demo-title" describedBy="demo-help" />
      </section>

      {toast !== null ? <Toast text={toast} onDismiss={dismissToast} /> : null}
    </div>

    <StickyBar>
      {backendMissing ? (
        <p className="kk-card-flat px-3 py-2 text-sm text-fg-2" role="alert">
          {t('upload.backendUnavailable')}
        </p>
      ) : null}
      {photos.length > 0 ? (
        <p className="text-center text-sm font-bold" aria-live="polite">
          {t('upload.photosCount', { n: photos.length })}
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
    </StickyBar>
    </>
  );
}
