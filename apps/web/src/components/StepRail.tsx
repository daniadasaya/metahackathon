import { useRouterState } from '@tanstack/react-router';
import { useT, type MessageKey } from '../lib/locale';

const STEPS: readonly MessageKey[] = ['header.stepUpload', 'header.stepProgress', 'header.stepReview', 'header.stepPreview', 'header.stepExport'];

function currentStep(pathname: string): number {
  if (pathname.startsWith('/jobs/')) return 2;
  if (pathname === '/review/preview') return 4;
  if (pathname.startsWith('/review')) return 3;
  if (pathname.startsWith('/export')) return 5;
  return 1;
}

export function StepRail() {
  const { t } = useT();
  const current = currentStep(useRouterState({ select: (state) => state.location.pathname }));
  return (
    <nav className="kk-rail" aria-label={t('header.stepsLabel')}>
      <ol className="kk-rail-list">
        {STEPS.map((key, index) => {
          const state = index + 1 < current ? 'done' : index + 1 === current ? 'current' : 'todo';
          return <li key={key} className="kk-rail-step" data-state={state} aria-current={state === 'current' ? 'step' : undefined} aria-label={t(key)}><span className="kk-rail-bar" aria-hidden="true" /></li>;
        })}
      </ol>
      <span className="kk-rail-count" aria-hidden="true">{current}/5</span>
    </nav>
  );
}
