import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { Column } from '../components/Column';
import { CheckIcon, CloseIcon, LayersIcon, PhotoIcon, PlusIcon, UploadIcon } from '../components/Icons';
import { StickyBar } from '../components/StickyBar';
import { ApiError, BackendUnavailableError, createJob, type ErrorCode, type SamplePack } from '../lib/api';
import { useT, type MessageKey } from '../lib/locale';
import { setStoredJobId, useStoredJobId } from '../lib/store';

interface PickedPhoto {
  id: number;
  file: File;
  caption: string;
}

type UploadFailure =
  | { kind: 'network' }
  | { kind: 'tooMany'; max: number; count: number }
  | { kind: 'tooLarge'; limit: string | null }
  | { kind: 'code'; code: ErrorCode | null };

const MAX_PHOTOS = 30;

const PACKS: ReadonlyArray<{ pack: SamplePack; label: MessageKey }> = [
  { pack: 'a', label: 'samples.packA' },
  { pack: 'b', label: 'samples.packB' },
  { pack: 'c', label: 'samples.packC' },
  { pack: 'd', label: 'samples.packD' },
];

function Thumb({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!file.type.startsWith('image/')) return;
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);

  return (
    <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-(--r-sm) bg-subtle text-fg-muted">
      {url === null || failed ? (
        <PhotoIcon />
      ) : (
        <img src={url} alt="" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      )}
    </span>
  );
}

/** Screen 1: pick photos (or a sample pack), add captions, start the job. */
export function UploadScreen() {
  const { t, errorText } = useT();
  const navigate = useNavigate();
  const storedJobId = useStoredJobId();

  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [general, setGeneral] = useState('');
  const [pack, setPack] = useState<SamplePack | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<UploadFailure | null>(null);
  const [rejectedPhotoId, setRejectedPhotoId] = useState<number | null>(null);
  const [backendMissing, setBackendMissing] = useState(false);
  const nextId = useRef(1);

  const invalidPhoto = photos.find((photo) => !photo.file.type.startsWith('image/') || photo.id === rejectedPhotoId);
  const validationProblem = pack !== null ? null : photos.length > MAX_PHOTOS
    ? t('upload.tooManyPhotos', { max: MAX_PHOTOS, count: photos.length })
    : invalidPhoto !== undefined ? t('upload.invalidPhoto', { name: invalidPhoto.file.name }) : null;
  const canStart = !busy && (pack !== null || photos.length > 0) && validationProblem === null;

  let failureText: string | null = null;
  if (failure?.kind === 'network') failureText = t('upload.networkFailed');
  else if (failure?.kind === 'tooMany') failureText = t('upload.tooManyPhotos', failure);
  else if (failure?.kind === 'tooLarge') failureText = failure.limit === null
    ? t('upload.photoTooLarge') : t('upload.photoTooLargeWithLimit', { limit: failure.limit });
  else if (failure?.kind === 'code') failureText = errorText(failure.code ?? 'internal_error');
  const problem = validationProblem ?? failureText;

  function addFiles(list: FileList | null) {
    if (list === null) return;
    const added: PickedPhoto[] = Array.from(list).map((file) => ({ id: nextId.current++, file, caption: '' }));
    if (added.length > 0) {
      setPhotos((current) => [...current, ...added]);
      setPack(null);
      setFailure(null);
      setBackendMissing(false);
    }
  }

  function setCaption(id: number, caption: string) {
    setPhotos((current) => current.map((photo) => (photo.id === id ? { ...photo, caption } : photo)));
  }

  function removePhoto(id: number) {
    setPhotos((current) => current.filter((photo) => photo.id !== id));
    setRejectedPhotoId((current) => current === id ? null : current);
    setFailure(null);
    setBackendMissing(false);
  }

  async function start() {
    if (!canStart) return;
    setBusy(true);
    setBackendMissing(false);
    setFailure(null);
    const submittedPhotos = pack === null ? photos : [];
    try {
      const jobId = await createJob({
        photos: submittedPhotos.map((photo) => photo.file),
        photoCaptions: submittedPhotos.map((photo) => photo.caption),
        generalCaption: pack === null ? general : '',
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
      if (error instanceof ApiError && error.status === 0) {
        setFailure({ kind: 'network' });
      } else if (error instanceof ApiError) {
        const message = error.serverMessage ?? '';
        const unreadable = /^Photo (\d+) \([\s\S]*\) is not a decodable image\.$/i.exec(message);
        const rejected = unreadable === null ? undefined : submittedPhotos[Number(unreadable[1]) - 1];
        const tooMany = /^Too many photos \(max (\d+)\)\.$/i.exec(message);
        const size = /(?:^|\s)(\d+(?:\.\d+)?)\s*([kmg]i?b|bytes?)\b/i.exec(message);
        if (error.status === 400 && error.code === 'invalid_request' && rejected !== undefined) {
          setRejectedPhotoId(rejected.id);
        } else if (error.status === 400 && error.code === 'invalid_request' && tooMany !== null) {
          setFailure({ kind: 'tooMany', max: Number(tooMany[1]), count: submittedPhotos.length });
        } else if (error.status === 413 || error.code === 'file_too_large') {
          setFailure({ kind: 'tooLarge', limit: size === null ? null : `${size[1]} ${size[2]}` });
        } else {
          setFailure({ kind: 'code', code: error.code });
        }
      } else {
        setFailure({ kind: 'code', code: null });
      }
      setBusy(false);
    }
  }

  return (
    <>
      <Column>
        <h1 className="text-2xl">{t('upload.title')}</h1>

        {storedJobId !== null ? (
          <section className="kk-resume mt-4" aria-labelledby="resume-title">
            <div className="flex items-start gap-3">
              <span className="kk-resume-icon" aria-hidden="true">
                <LayersIcon size={18} />
              </span>
              <div className="min-w-0">
                <h2 id="resume-title" className="text-base font-bold">
                  {t('upload.resumeTitle')}
                </h2>
                <p className="text-base text-fg-2">{t('upload.resumeHint')}</p>
              </div>
            </div>
            <div className="mt-3 flex items-center justify-between gap-2">
              <button type="button" className="kk-btn kk-btn-ghost kk-tactile px-2" onClick={() => setStoredJobId(null)}>
                {t('upload.resumeDismiss')}
              </button>
              <button
                type="button"
                className="kk-btn kk-btn-secondary kk-tactile"
                onClick={() => void navigate({ to: '/jobs/$jobId', params: { jobId: storedJobId } })}
              >
                {t('upload.resumeOpen')}
              </button>
            </div>
          </section>
        ) : null}

        <label className="kk-drop kk-card-tap kk-tactile mt-4" data-filled={photos.length > 0}>
          <span className="kk-icon-tile">
            {photos.length > 0 ? <PlusIcon size={20} /> : <UploadIcon size={22} />}
          </span>
          <span className="kk-drop-title">{photos.length > 0 ? t('upload.addMore') : t('upload.dropTitle')}</span>
          <span className="kk-drop-hint">{t('upload.dropHint')}</span>
          <input
            type="file"
            accept="image/*"
            multiple
            className="sr-only"
            aria-label={t('upload.dropTitle')}
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
            <ul className="kk-photo-strip m-0 mt-3 flex list-none gap-3 p-0">
              {photos.map((photo, index) => (
                <li key={photo.id} className="kk-card-flat kk-photo-strip-item m-0 p-4">
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
                  {!photo.file.type.startsWith('image/') || photo.id === rejectedPhotoId ? (
                    <p className="mt-3 text-sm text-fg-2 break-words">{t('upload.invalidPhoto', { name: photo.file.name })}</p>
                  ) : null}
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
          <p id="samples-hint" className="mt-1 text-sm text-fg-muted">
            {t('samples.hint')}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-3">
            {PACKS.map((entry) => {
              const selected = pack === entry.pack;
              return (
                <button
                  key={entry.pack}
                  type="button"
                  data-sample={entry.pack}
                  className="kk-pack kk-tactile"
                  aria-pressed={selected}
                  aria-label={`${t('samples.packTag', { pack: entry.pack.toUpperCase() })}: ${t(entry.label)}`}
                  onClick={() => {
                    setPack(selected ? null : entry.pack);
                    setFailure(null);
                    setBackendMissing(false);
                  }}
                >
                  <span className="kk-pack-media" aria-hidden="true">
                    <img src={`/samples/thumbs/${entry.pack}.jpg`} alt="" width={640} height={480} loading="lazy" decoding="async" />
                    <span className="kk-pack-check" aria-hidden="true">
                      <CheckIcon size={12} />
                    </span>
                  </span>
                  <span className="kk-pack-meta" aria-hidden="true">
                    <span className="kk-pack-tag">
                      {t('samples.packTag', { pack: entry.pack.toUpperCase() })}
                    </span>
                    <span className="kk-pack-name">{t(entry.label)}</span>
                  </span>
                </button>
              );
            })}
          </div>
          {pack !== null ? (
            <p className="mt-2 text-base text-fg-2">{t('samples.selected', { pack: pack.toUpperCase() })}</p>
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
            <p className="mt-2 text-base text-fg-muted">{t('upload.captionHelp')}</p>
          </div>
        </details>

      </Column>

      <StickyBar>
        {problem !== null ? (
          <p id="upload-problem" className="kk-toast px-3 py-2 text-sm break-words" role="alert">{problem}</p>
        ) : null}
        {backendMissing ? (
          <p className="kk-card-flat px-3 py-2 text-sm text-fg-2" role="alert">
            {t('upload.backendUnavailable')}
          </p>
        ) : null}
        <button
          type="button"
          className="kk-btn kk-btn-primary kk-tactile w-full"
          disabled={!canStart}
          aria-describedby={problem !== null ? 'upload-problem' : undefined}
          onClick={() => void start()}
        >
          {busy ? t('upload.ctaBusy') : failure?.kind === 'network' ? t('common.retry') : t('upload.cta')}
        </button>
        {!canStart && !busy && problem === null ? <p className="text-center text-sm text-fg-muted">{t('upload.ctaNeed')}</p> : null}
      </StickyBar>
    </>
  );
}
