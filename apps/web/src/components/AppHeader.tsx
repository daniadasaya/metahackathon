import { Link, useRouterState } from '@tanstack/react-router';

import { useT } from '../lib/locale';
import { nextTheme, setLocale, setTheme, useLocale, useTheme, type Theme } from '../lib/store';
import { MoonIcon, SunIcon, SystemIcon } from './Icons';
import { Wordmark } from './Logo';

const TOTAL_STEPS = 5;

/** Upload → Progress → Review → Preview → Export, by path. Purely visual: no router state is changed. */
function stepOf(pathname: string): number {
  if (pathname.startsWith('/jobs/')) return 2;
  if (pathname === '/review/preview') return 4;
  if (pathname.startsWith('/review')) return 3;
  if (pathname.startsWith('/export')) return 5;
  return 1;
}

function ThemeIcon({ theme }: { theme: Theme }) {
  if (theme === 'light') return <SunIcon />;
  if (theme === 'dark') return <MoonIcon />;
  return <SystemIcon />;
}

/** Sticky app header, shared by every screen: lockup, locale switch, theme cycle, step indicator. */
export function AppHeader() {
  const { t } = useT();
  const locale = useLocale();
  const theme = useTheme();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const step = stepOf(pathname);

  const themeLabel =
    theme === 'light' ? t('header.themeLight') : theme === 'dark' ? t('header.themeDark') : t('header.themeSystem');

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-canvas">
      <div className="mx-auto flex min-h-12 w-full max-w-[420px] md:max-w-none items-center justify-between gap-2 px-4">
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
            className="kk-btn kk-btn-secondary kk-btn-icon kk-tactile"
            aria-label={t('header.themeSwitch', { current: themeLabel })}
            title={themeLabel}
            onClick={() => setTheme(nextTheme(theme))}
          >
            <ThemeIcon theme={theme} />
          </button>
        </div>
      </div>
      {/* The step rail sits on the header's bottom edge: progress without a second row. */}
      <p className="sr-only">{t('header.step', { n: step, total: TOTAL_STEPS })}</p>
      <div className="kk-steps" aria-hidden="true">
        {Array.from({ length: TOTAL_STEPS }, (_, index) => (
          <span key={index} data-state={index + 1 < step ? 'done' : index + 1 === step ? 'current' : 'todo'} />
        ))}
      </div>
    </header>
  );
}
