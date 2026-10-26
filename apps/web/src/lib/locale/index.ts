/**
 * The one dictionary lookup. Components call `useT()` and never hold a string literal.
 * Switching locale re-renders subscribers only: no reload, no refetch, no lost state.
 */
import type { components } from '../types';
import { useLocale, type Locale } from '../store';
import type { Paths } from './check';
import { en } from './en';
import { id } from './id';

export type Dictionary = typeof id;
export type MessageKey = Paths<Dictionary>;
export type ErrorCode = components['schemas']['ErrorCode'];
export type Params = Record<string, string | number>;

const dictionaries: Record<Locale, Dictionary> = { id, en };

function lookup(dictionary: Dictionary, key: string): string | undefined {
  let node: unknown = dictionary;
  for (const part of key.split('.')) {
    if (typeof node !== 'object' || node === null) return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === 'string' ? node : undefined;
}

function interpolate(template: string, params: Params | undefined): string {
  if (params === undefined) return template;
  return template.replace(/\{(\w+)\}/g, (slot, name: string) => {
    const value = params[name];
    return value === undefined ? slot : String(value);
  });
}

export interface Translator {
  locale: Locale;
  t: (key: MessageKey, params?: Params) => string;
  /**
   * Translated text for a server error `code`. A code with no key returns `null` and the
   * caller renders nothing. The server's `message` is never an input here.
   */
  errorText: (code: string) => string | null;
}

export function createTranslator(locale: Locale): Translator {
  const dictionary = dictionaries[locale];
  return {
    locale,
    t: (key, params) => interpolate(lookup(dictionary, key) ?? '', params),
    errorText: (code) => {
      const text = lookup(dictionary, `errors.${code}`);
      return text === undefined ? null : text;
    },
  };
}

const translators: Record<Locale, Translator> = {
  id: createTranslator('id'),
  en: createTranslator('en'),
};

export function useT(): Translator {
  return translators[useLocale()];
}
