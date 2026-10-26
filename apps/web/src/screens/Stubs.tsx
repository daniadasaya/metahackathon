import { Link } from '@tanstack/react-router';
import type { ReactNode } from 'react';

import { useT, type MessageKey } from '../lib/locale';

interface StubScreenProps {
  titleKey: Extract<MessageKey, 'review.title' | 'export.title'>;
  bodyKey: Extract<MessageKey, 'review.stub' | 'export.stub'>;
}

function Column({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-[420px] px-4 pt-6 pb-16">{children}</div>;
}

/** Titled placeholder for a screen that arrives in a later task, so navigation never 404s. */
export function StubScreen({ titleKey, bodyKey }: StubScreenProps) {
  const { t } = useT();
  return (
    <Column>
      <h1 className="text-2xl">{t(titleKey)}</h1>
      <p className="mt-3 text-base text-fg-2">{t(bodyKey)}</p>
      <Link to="/" className="kk-btn kk-btn-secondary kk-tactile mt-6">
        {t('common.back')}
      </Link>
    </Column>
  );
}

export function ReviewScreen() {
  return <StubScreen titleKey="review.title" bodyKey="review.stub" />;
}

export function ExportScreen() {
  return <StubScreen titleKey="export.title" bodyKey="export.stub" />;
}
