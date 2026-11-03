import { useRouterState } from '@tanstack/react-router';

import { useT, type MessageKey } from '../lib/locale';

const STEPS: readonly MessageKey[] = [
  'header.stepUpload',
  'header.stepProgress',
  'header.stepReview',
  'header.stepPreview',
  'header.stepExport',
];

/** Upload → Progress → Review → Preview → Export, by path. Purely visual: no router state is changed. */
function currentStep(pathname: string): number {
  if (pathname.startsWith('/jobs/')) return 2;
  if (pathname === '/review/preview') return 4;
  if (pathname.startsWith('/review')) return 3;
  if (pathname.startsWith('/export')) return 5;
  return 1;
}

/**
 * The five named steps under the header. Each step is a bar over its own label, in a five-column grid,
 * so labels never share a row and cannot run into each other. Done and waiting steps are announced to screen readers; the current step carries
 * `aria-current`, and its bold ink label is the visible cue.
 */
export function StepRail() {
  const { t } = useT();
  const current = currentStep(useRouterState({ select: (state) => state.location.pathname }));

  return (
    <nav className="kk-rail" aria-label={t('header.stepsLabel')}>
      <ol className="kk-rail-list">
        {STEPS.map((key, index) => {
          const position = index + 1;
          const state = position < current ? 'done' : position === current ? 'current' : 'todo';
          return (
            <li
              key={key}
              className="kk-rail-step"
              data-state={state}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              <span className="kk-rail-bar" aria-hidden="true" />
              <span className="kk-rail-label">{t(key)}</span>
              {state === 'current' ? null : (
                <span className="sr-only">{t(state === 'done' ? 'header.stepDone' : 'header.stepTodo')}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
