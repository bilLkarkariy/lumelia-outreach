/*
 * Shapes shared by every screen.
 *
 * The API returns plain JSON and the UI reads it as loose records: there is no generated client yet,
 * so this file is the only place where `any` is allowed. Narrow a record where a screen depends on it.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = any;
export type Row = Record<string, any>;
/* eslint-enable @typescript-eslint/no-explicit-any */

export type Api = (path: string, method?: string, data?: unknown) => Promise<Json>;
export type Upload = (path: string, body: FormData) => Promise<Json>;
/** Runs a mutation, then reloads shared data and reports `success` (or the failure) to the user. */
export type Action = (fn: () => Promise<unknown>, success?: string) => Promise<void>;

/** Message of a thrown value, empty when it carries none. */
export function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : typeof reason === 'string' ? reason : '';
}
