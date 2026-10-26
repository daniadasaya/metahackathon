import { Link } from '@tanstack/react-router';

import { useT } from '../lib/locale';

/** Titled placeholder for the export screen (live probe), so navigation from Review never dead-ends. */
export function ExportScreen() {
  const { t } = useT();
  return (
    <div className="mx-auto w-full max-w-[420px] px-4 pt-6 pb-16">
      <h1 className="text-2xl">{t('export.title')}</h1>
      <p className="mt-3 text-base text-fg-2">{t('export.stub')}</p>
      <Link to="/review" className="kk-btn kk-btn-secondary kk-tactile mt-6">
        {t('common.back')}
      </Link>
    </div>
  );
}
