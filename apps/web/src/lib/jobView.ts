/**
 * Pure derivations from a polled `JobState` for the progress screen. No fetching, no strings.
 */
import type { JobState } from './api';
import type { TileState } from '../components/StageStrip';
import type { components } from './types';

export type JobStage = components['schemas']['JobStage'];
export type StageError = components['schemas']['StageError'];

/** Pipeline order; the index doubles as the A–E tile position. */
export const STAGE_ORDER: readonly JobStage[] = ['concepts', 'segment', 'cleanup', 'extract', 'done'];

export function stageIndex(stage: JobStage): number {
  return STAGE_ORDER.indexOf(stage);
}

/**
 * One state per tile. A stage that has at least one error shows `failed`, whatever else is true:
 * the failure is per item, so the rest of the strip keeps its own progress.
 */
export function tileStates(job: JobState): TileState[] {
  const failedStages = new Set(job.errors.map((error) => error.stage));
  const current = stageIndex(job.stage);

  return STAGE_ORDER.map((stage, index): TileState => {
    if (failedStages.has(stage)) return 'failed';
    if (job.status === 'done') return 'done';
    if (job.status === 'queued') return 'pending';
    if (index < current) return 'done';
    if (index === current) return job.status === 'failed' ? 'pending' : 'active';
    return 'pending';
  });
}

/** Errors in reading order: by photo, then by pipeline stage. */
export function sortedErrors(errors: readonly StageError[]): StageError[] {
  return [...errors].sort(
    (a, b) => a.photo_index - b.photo_index || stageIndex(a.stage) - stageIndex(b.stage),
  );
}
