import type {Row} from './ListTable';

/**
 * Explains why outbound calling stays closed for a prospect record, so the call
 * panel never invites an action the space has already ruled out.
 * Returns undefined when the record can be called.
 */
export function callBlockedReason(record: Row): string | undefined {
  const account: Row = record.account || {};
  if (record.paused || record.status === 'suppressed')
    return 'Ce contact est en pause ou s’est opposé à la prospection.';
  if (account.lifecycle && account.lifecycle !== 'prospect')
    return (
      account.pause_reason ||
      (account.lifecycle === 'excluded'
        ? 'Cette entreprise est exclue de la prospection.'
        : 'Cette entreprise n’est pas dans le périmètre de prospection.')
    );
  if (account.outreach_paused)
    return account.pause_reason || 'La prospection de cette entreprise est suspendue.';
  if (record.stage === 'excluded' || record.stage === 'not_fit')
    return record.next_action || 'Cette entreprise est hors cible.';
  return undefined;
}
