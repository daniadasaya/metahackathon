import { CheckIcon, RetryIcon } from './Icons';
import { STAGE_LETTERS } from '../lib/display';

export type TileState = 'done' | 'active' | 'pending' | 'failed';

interface StageStripProps {
  /** Stage names, translated, in A–E order. */
  stageNames: readonly string[];
  /** State words, translated, for assistive tech. */
  stateNames: Record<TileState, string>;
  label: string;
  states: readonly TileState[];
}

/** Five 56×56 tiles with 6px rules between them: 5×56 + 4×6 = 304px, inside the 328px column. */
export function StageStrip({ stageNames, stateNames, label, states }: StageStripProps) {
  return (
    <ol className="m-0 flex list-none items-center p-0" aria-label={label}>
      {STAGE_LETTERS.map((letter, index) => {
        const state = states[index] ?? 'pending';
        const ruleState = states[index - 1] === 'done' ? 'done' : 'pending';
        return (
          <li key={letter} className="m-0 flex items-center p-0">
            {index > 0 ? <span className="kk-rule" data-state={ruleState} aria-hidden="true" /> : null}
            <span
              className="kk-tile"
              data-state={state}
              role="img"
              aria-label={`${letter}: ${stageNames[index] ?? ''}, ${stateNames[state]}`}
            >
              {state === 'done' ? <CheckIcon size={28} /> : null}
              {state === 'failed' ? <RetryIcon size={26} /> : null}
              {state === 'active' || state === 'pending' ? letter : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
