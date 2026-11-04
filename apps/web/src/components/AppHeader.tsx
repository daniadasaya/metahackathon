import { Link, useRouterState } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { useT, type MessageKey } from '../lib/locale';
import { setLocale, setTheme, THEME_ORDER, useLocale, useStoredJobId, useTheme, type Theme } from '../lib/store';
import { ArrowLeftIcon, CloseIcon, SettingsIcon } from './Icons';
import { Wordmark } from './Logo';
import { StepRail } from './StepRail';

const THEME_NAME: Record<Theme, MessageKey> = {
  system: 'header.themeSystem',
  light: 'header.themeLight',
  dark: 'header.themeDark',
};

function SettingsSheet({ close }: { close: () => void }) {
  const { t } = useT();
  const locale = useLocale();
  const theme = useTheme();
  const sheet = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    sheet.current?.querySelector<HTMLButtonElement>('button')?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (event.key !== 'Tab' || sheet.current === null) return;
      const controls = Array.from(sheet.current.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, [close]);

  return (
    <div className="kk-sheet-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <div ref={sheet} className="kk-sheet" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div className="kk-sheet-grabber" aria-hidden="true" />
        <div className="kk-sheet-header">
          <h2 id="settings-title" className="kk-sheet-title">{t('header.settings')}</h2>
          <button type="button" className="kk-btn kk-btn-ghost kk-btn-icon" aria-label={t('common.close')} onClick={close}><CloseIcon /></button>
        </div>
        <div className="kk-setting-group">
          <span className="kk-setting-label">{t('header.language')}</span>
          <div className="kk-seg" role="group" aria-label={t('header.language')}>
            <button type="button" aria-pressed={locale === 'id'} aria-label={`${t('header.codeId')} · ${t('header.languageId')}`} onClick={() => setLocale('id')}>{t('header.codeId')}</button>
            <button type="button" aria-pressed={locale === 'en'} aria-label={`${t('header.codeEn')} · ${t('header.languageEn')}`} onClick={() => setLocale('en')}>{t('header.codeEn')}</button>
          </div>
        </div>
        <div className="kk-setting-group">
          <span className="kk-setting-label">{t('header.theme')}</span>
          <div className="kk-seg" role="group" aria-label={t('header.theme')}>
            {THEME_ORDER.map((value) => <button key={value} type="button" aria-pressed={theme === value} onClick={() => setTheme(value)}>{t(THEME_NAME[value])}</button>)}
          </div>
        </div>
      </div>
    </div>
  );
}

export function AppHeader() {
  const { t } = useT();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const jobId = useStoredJobId();
  const [open, setOpen] = useState(false);
  const close = useRef(() => setOpen(false)).current;
  const isUpload = pathname === '/';
  const title: MessageKey = pathname.startsWith('/jobs/') ? 'header.stepProgress'
    : pathname === '/review/preview' ? 'header.stepPreview'
      : pathname.startsWith('/review') ? 'header.stepReview' : 'header.stepExport';

  return (
    <header className="kk-header">
      <div className="kk-header-row">
        {isUpload ? <Link to="/" className="kk-header-title inline-flex min-h-11 items-center" aria-label={t('app.name')}><Wordmark name={t('app.name')} /></Link> : (
          <>
            {pathname.startsWith('/review') && pathname !== '/review/preview' && jobId !== null ? (
              <Link to="/jobs/$jobId" params={{ jobId }} className="kk-btn kk-btn-ghost kk-btn-icon kk-header-back" aria-label={t('common.back')}><ArrowLeftIcon /></Link>
            ) : (
              <Link to={pathname.startsWith('/export') ? '/review/preview' : pathname === '/review/preview' ? '/review' : '/'} className="kk-btn kk-btn-ghost kk-btn-icon kk-header-back" aria-label={t('common.back')}><ArrowLeftIcon /></Link>
            )}
            {pathname === '/review' || pathname === '/export'
              ? <h1 className="kk-header-title">{t(title)}</h1>
              : <span className="kk-header-title">{t(title)}</span>}
          </>
        )}
        <StepRail />
        <button type="button" className="kk-btn kk-btn-ghost kk-btn-icon kk-header-settings" aria-label={t('header.settings')} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}><SettingsIcon /></button>
      </div>
      {open ? <SettingsSheet close={close} /> : null}
    </header>
  );
}
