// How a problem's path finds its field on the page.

import type { FormError } from '../planForm'

// The element id of the field a problem's path names: "slots.0.kcal_share" is
// "f-slots-0-kcal_share".
export function fieldId(path: string): string {
  return path === '' ? 'plan-form' : `f-${path.replaceAll('.', '-')}`
}

export function errorId(path: string): string {
  return `${fieldId(path)}-error`
}

// Problems by the field they belong to.
export type Errors = Map<string, string[]>

export function byField(errors: FormError[]): Errors {
  const map: Errors = new Map()
  for (const { path, message } of errors) map.set(path, [...(map.get(path) ?? []), message])
  return map
}

// Moves to a field from a link in the list of problems, without changing the address: the
// address after "#" is the screen.
export function goToField(id: string): void {
  const target = document.getElementById(id)
  if (target === null) return
  target.scrollIntoView?.({ block: 'center' })
  target.focus()
}

// After a control the focus was on is removed, such as a row's Remove button, the focus goes to
// the element with this id once the page has updated, rather than to the page itself.
export function focusSoon(id: string): void {
  setTimeout(() => document.getElementById(id)?.focus(), 0)
}
