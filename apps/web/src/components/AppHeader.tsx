import { Link } from '@tanstack/react-router';

import { useT, type MessageKey } from '../lib/locale';
import { setLocale, setTheme, THEME_ORDER, useLocale, useTheme, type Theme } from '../lib/store';
import { MoonIcon, SunIcon, SystemIcon } from './Icons';
import { Wordmark } from './Logo';

const THEME_NAME: Record<Theme, MessageKey> = {
  system: 'header.themeSystem',
  light: 'header.themeLight',
  dark: 'header.themeDark',
};

const THEME_ICON: Record<Theme, typeof SunIcon> = {
  system: SystemIcon,
  light: SunIcon,
  dark: MoonIcon,
};

/**
 * Sticky app header: the lockup, the language switch, and a labelled theme menu. The menu shows the
 * active mode by name and icon (System, Light or Dark), and opening it lists all three. `kk:theme`
 * persists the choice; `system` follows the OS live.
 */
export function AppHeader() {
  const { t } = useT();
  const locale = useLocale();
  const theme = useTheme();
  const ModeIcon = THEME_ICON[theme];

  return (
    <header className="sticky top-(--kk-status-inset) z-10 border-b border-line bg-canvas">
      <div className="mx-auto flex min-h-12 w-full max-w-[420px] md:max-w-none items-center justify-between gap-2 px-4">
        <Link to="/" className="inline-flex min-h-11 shrink-0 items-center rounded-[12px]" aria-label={t('app.name')}>
          <Wordmark name={t('app.name')} />
        </Link>
        <div className="flex shrink-0 items-center gap-2">
          {/* The accessible name starts with the visible code, as WCAG 2.5.3 (Label in Name) requires. */}
          <div className="kk-seg" role="group" aria-label={t('header.language')}>
            <button
              type="button"
              aria-pressed={locale === 'id'}
              aria-label={`${t('header.codeId')} · ${t('header.languageId')}`}
              onClick={() => setLocale('id')}
            >
              {t('header.codeId')}
            </button>
            <button
              type="button"
              aria-pressed={locale === 'en'}
              aria-label={`${t('header.codeEn')} · ${t('header.languageEn')}`}
              onClick={() => setLocale('en')}
            >
              {t('header.codeEn')}
            </button>
          </div>
          <div className="kk-theme">
            <span className="kk-theme-icon" aria-hidden="true">
              <ModeIcon size={16} />
            </span>
            <select
              className="kk-theme-field"
              aria-label={t('header.theme')}
              value={theme}
              onChange={(event) => {
                const next = THEME_ORDER.find((value) => value === event.target.value);
                if (next !== undefined) setTheme(next);
              }}
            >
              {THEME_ORDER.map((value) => (
                <option key={value} value={value}>
                  {t(THEME_NAME[value])}
                </option>
              ))}
            </select>
            <span className="kk-theme-caret" aria-hidden="true" />
          </div>
        </div>
      </div>
    </header>
  );
}
