import {useEffect} from 'react';
import './validation.css';

/**
 * The workspace forms rely on native browser validation. Chrome announces one
 * transient bubble at a time, leaves no trace in the DOM, and marks no field as
 * invalid for assistive technology. This module keeps the native behaviour and
 * adds what is missing: a persistent inline message per field, aria-invalid on
 * the control, and one announced summary at the top of the form.
 */

type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
const FIELD_SELECTOR = 'input, select, textarea';
const SUMMARY_ATTRIBUTE = 'data-form-error-summary';
const ERROR_ATTRIBUTE = 'data-field-error';

function isField(node: EventTarget | null): node is Field {
  return node instanceof HTMLElement && node.matches(FIELD_SELECTOR);
}

function describe(field: Field): string {
  const validity = field.validity;
  if (validity.valueMissing) return 'Ce champ est obligatoire.';
  if (validity.typeMismatch) {
    if (field instanceof HTMLInputElement && field.type === 'email')
      return 'Format attendu : nom@example.com.';
    if (field instanceof HTMLInputElement && field.type === 'url')
      return 'Adresse web attendue, par exemple https://exemple.fr.';
    return 'Format attendu non respecté.';
  }
  if (validity.patternMismatch) return 'Format attendu non respecté.';
  if (validity.tooShort && 'minLength' in field)
    return 'Saisissez au moins ' + field.minLength + ' caractères.';
  if (validity.tooLong && 'maxLength' in field) return 'Maximum ' + field.maxLength + ' caractères.';
  if (validity.rangeUnderflow && 'min' in field) return 'Valeur minimale : ' + field.min + '.';
  if (validity.rangeOverflow && 'max' in field) return 'Valeur maximale : ' + field.max + '.';
  if (validity.badInput) return 'Saisie non reconnue.';
  return 'Vérifiez ce champ.';
}

/** The visible label of a control, without the error text this module adds. */
function caption(field: Field): string {
  const labels = field.labels;
  if (labels && labels.length) {
    const clone = labels[0].cloneNode(true) as HTMLElement;
    clone.querySelectorAll('[' + ERROR_ATTRIBUTE + ']').forEach((node) => node.remove());
    const text = (clone.textContent || '').replace(/\s+/g, ' ').trim();
    if (text) return text.slice(0, 80);
  }
  const placeholder = field.getAttribute('placeholder');
  if (placeholder) return placeholder.slice(0, 80);
  return field.getAttribute('name') || 'Champ';
}

function noteFor(field: Field): HTMLElement | null {
  const id = field.getAttribute('aria-describedby');
  if (!id) return null;
  const node = document.getElementById(id.split(' ')[0]);
  return node && node.hasAttribute(ERROR_ATTRIBUTE) ? node : null;
}

function decorate(field: Field) {
  const message = describe(field);
  field.setAttribute('aria-invalid', 'true');
  field.classList.add('field-invalid');
  let note = noteFor(field);
  if (!note) {
    note = document.createElement('p');
    note.setAttribute(ERROR_ATTRIBUTE, '');
    note.className = 'field-error';
    if (!field.id) field.id = 'field-' + Math.random().toString(36).slice(2, 9);
    note.id = field.id + '-error';
    const host = field.parentElement && field.parentElement.tagName === 'LABEL' ? field.parentElement : field;
    host.insertAdjacentElement('afterend', note);
    const described = (field.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
    field.setAttribute('aria-describedby', [note.id, ...described].join(' '));
  }
  if (note.textContent !== message) note.textContent = message;
}

function clear(field: Field) {
  const note = noteFor(field);
  if (note) {
    const remaining = (field.getAttribute('aria-describedby') || '')
      .split(' ')
      .filter((id) => id && id !== note.id);
    if (remaining.length) field.setAttribute('aria-describedby', remaining.join(' '));
    else field.removeAttribute('aria-describedby');
    note.remove();
  }
  field.removeAttribute('aria-invalid');
  field.classList.remove('field-invalid');
}

function rebuildSummary(form: HTMLFormElement, fields: Field[]) {
  const invalid = fields.filter(
    (field) => field.form === form && field.willValidate && !field.validity.valid,
  );
  const existing = form.querySelector<HTMLElement>('[' + SUMMARY_ATTRIBUTE + ']');
  if (!invalid.length) {
    existing?.remove();
    return;
  }
  const summary = existing || document.createElement('div');
  if (!existing) {
    summary.setAttribute(SUMMARY_ATTRIBUTE, '');
    summary.className = 'form-error-summary';
    summary.setAttribute('role', 'alert');
    summary.tabIndex = -1;
    form.prepend(summary);
  }
  const count = invalid.length;
  summary.innerHTML = '';
  const title = document.createElement('p');
  title.innerHTML =
    '<strong>' +
    (count > 1 ? 'Vérifiez ces ' + count + ' champs' : 'Vérifiez ce champ') +
    ' avant d’enregistrer.</strong>';
  summary.append(title);
  const list = document.createElement('ul');
  invalid.forEach((field) => {
    const item = document.createElement('li');
    const link = document.createElement('a');
    link.href = '#' + field.id;
    link.textContent = caption(field) + ' : ' + describe(field);
    link.addEventListener('click', (event) => {
      event.preventDefault();
      field.focus();
    });
    item.append(link);
    list.append(item);
  });
  summary.append(list);
}

/** Improves native form validation for every form of the workspace. */
export function useFormValidationFeedback() {
  useEffect(() => {
    const marked = new Set<Field>();
    let pending = new Set<HTMLFormElement>();
    let frame = 0;

    function flush() {
      frame = 0;
      pending.forEach((form) => rebuildSummary(form, Array.from(marked)));
      pending = new Set();
    }
    function schedule(form: HTMLFormElement | null) {
      if (!form) return;
      pending.add(form);
      if (!frame) frame = requestAnimationFrame(flush);
    }
    function onInvalid(event: Event) {
      if (!isField(event.target)) return;
      decorate(event.target);
      marked.add(event.target);
      schedule(event.target.form);
    }
    function onValueChange(event: Event) {
      if (!isField(event.target) || !marked.has(event.target)) return;
      if (event.target.willValidate && event.target.validity.valid) {
        clear(event.target);
        marked.delete(event.target);
      } else {
        decorate(event.target);
      }
      schedule(event.target.form);
    }
    function onReset(event: Event) {
      const form = event.target instanceof HTMLFormElement ? event.target : null;
      if (!form) return;
      marked.forEach((field) => {
        if (field.form === form) {
          clear(field);
          marked.delete(field);
        }
      });
      schedule(form);
    }
    document.addEventListener('invalid', onInvalid, true);
    document.addEventListener('input', onValueChange, true);
    document.addEventListener('change', onValueChange, true);
    document.addEventListener('reset', onReset, true);
    return () => {
      document.removeEventListener('invalid', onInvalid, true);
      document.removeEventListener('input', onValueChange, true);
      document.removeEventListener('change', onValueChange, true);
      document.removeEventListener('reset', onReset, true);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
}
