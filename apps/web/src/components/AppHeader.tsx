import { Link } from '@tanstack/react-router';

import { useT } from '../lib/locale';
import { nextTheme, setLocale, setTheme, useLocale, useTheme, type Theme } from '../lib/store';
import { MoonIcon, SunIcon, SystemIcon } from './Icons';
import { Wordmark } from './Logo';

function ThemeIcon({ theme }: { theme: Theme }) {
  if (theme === 'light') return <SunIcon />;
  if (theme === 'dark') return <MoonIcon />;
  return <SystemIcon />;
}

/** Sticky app header, shared by every screen: lockup, locale switch, theme cycle. */
export function AppHeader() {
  const { t } = useT();
  const locale = useLocale();
  const theme = useTheme();

  const themeLabel =
    theme === 'light' ? t('header.themeLight') : theme === 'dark' ? t('header.themeDark') : t('header.themeSystem');

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-canvas">
      <div className="mx-auto flex min-h-14 w-full max-w-[420px] items-center justify-between gap-2 px-4">
        <Link to="/" className="inline-flex min-h-11 items-center rounded-[12px]" aria-label={t('app.name')}>
          <Wordmark name={t('app.name')} />
        </Link>
        <div className="flex items-center gap-2">
          <div className="kk-seg" role="group" aria-label={t('header.language')}>
            <button
              type="button"
              aria-pressed={locale === 'id'}
              aria-label={t('header.languageId')}
              onClick={() => setLocale('id')}
            >
              ID
            </button>
            <button
              type="button"
              aria-pressed={locale === 'en'}
              aria-label={t('header.languageEn')}
              onClick={() => setLocale('en')}
            >
              EN
            </button>
          </div>
          <button
            type="button"
            className="kk-btn kk-btn-secondary kk-tactile"
            style={{ minHeight: 44, minWidth: 44, padding: 0, borderRadius: 14 }}
            aria-label={t('header.themeSwitch', { current: themeLabel })}
            title={themeLabel}
            onClick={() => setTheme(nextTheme(theme))}
          >
            <ThemeIcon theme={theme} />
          </button>
        </div>
      </div>
    </header>
  );
}
