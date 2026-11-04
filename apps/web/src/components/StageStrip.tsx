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

/** Each stage has a visible name and state in addition to its status icon. */
export function StageStrip({ stageNames, stateNames, label, states }: StageStripProps) {
  return (
    <ol className="kk-stage-list" aria-label={label}>
      {STAGE_LETTERS.map((letter, index) => {
        const state = states[index] ?? 'pending';
        const ruleState = states[index - 1] === 'done' ? 'done' : 'pending';
        return (
          <li key={letter} className="kk-stage-row">
            {index > 0 ? <span className="kk-rule" data-state={ruleState} aria-hidden="true" /> : null}
            <span
              className="kk-tile"
              data-state={state}
              role="img"
              aria-label={`${letter}: ${stageNames[index] ?? ''}, ${stateNames[state]}`}
            >
              {state === 'done' ? <CheckIcon size={20} /> : null}
              {state === 'failed' ? <RetryIcon size={18} /> : null}
              {state === 'active' || state === 'pending' ? letter : null}
            </span>
            <span className="kk-stage-copy"><strong>{stageNames[index]}</strong><small>{stateNames[state]}</small></span>
          </li>
        );
      })}
    </ol>
  );
}
