// A swapped meal as the person confirms it before it is saved: the meal reader's items, each
// found, needing an amount, in a unit nobody has, or missing, and what the person did to each.
// A food the data lacks stays missing: it is listed, never sent, never saved and never replaced.
// The numbers shown for an item are nutrition-mcp's, from the read or the compute route.

import type { LogItem, NutritionResult, ReadItem, ReadResult } from './types'

export interface SwapItem {
  key: number
  // The name as the reader read it, in the person's words.
  name: string
  // `matched` once the food, the amount and the unit are ones nutrition-mcp reads; the reader's
  // other statuses until the person gives what is missing.
  status: ReadItem['status']
  food: { id: string; name: string } | null
  // As the person gave them, never the grams the server turned them into.
  amount: number | null
  unit: string | null
  // The units the person may choose from for the food.
  units: string[]
  numbers: NutritionResult | null
  confirmed: boolean
}

// The person's change to an item: the food, how many of which unit, and the food's units.
export interface Change {
  food: { id: string; name: string }
  amount: number
  unit: string
  units: string[]
}

function unitsOf(item: ReadItem): string[] {
  if (item.status !== 'matched') return item.valid_units
  // The reader lists no units for a food it matched; nutrition-mcp's are in its numbers.
  const own = Object.keys(item.nutrition?.food.units ?? {})
  return [...new Set([...(item.unit === null ? [] : [item.unit]), ...own])]
}

export function swapItems(result: ReadResult): SwapItem[] {
  return result.items.map((item, key) => ({
    key,
    name: item.name,
    status: item.status,
    food: item.food_id === null ? null : { id: item.food_id, name: item.food_name ?? item.food_id },
    amount: item.amount,
    unit: item.unit,
    units: unitsOf(item),
    numbers: item.nutrition,
    confirmed: false,
  }))
}

// What an item is called on the screen: its food's name, or the words typed for a missing one.
export function itemName(item: SwapItem): string {
  return item.food?.name ?? item.name
}

// The items nutrition-mcp can work out: what the compute route is sent and what is saved.
export function counted(items: SwapItem[]): SwapItem[] {
  return items.filter((item) => item.status === 'matched')
}

// What still stops the meal from being saved, in the order of the items. `changing` holds the
// keys of the found items the person has opened to change.
export function stillToDo(items: SwapItem[], changing: ReadonlySet<number>): string[] {
  const todo: string[] = []
  for (const item of items) {
    const name = itemName(item)
    if (item.status === 'needs_amount') todo.push(`give an amount for ${name}`)
    else if (item.status === 'unknown_unit') todo.push(`choose a unit for ${name}`)
    else if (item.status === 'matched' && changing.has(item.key))
      todo.push(`finish changing ${name}`)
    else if (item.status === 'matched' && !item.confirmed) todo.push(`confirm ${name}`)
  }
  return todo
}

// The items to send to the compute route with `change` made to the item `key`, in their order.
export function withChange(items: SwapItem[], key: number, change: Change): SwapItem[] {
  return items.map((item) =>
    item.key === key ? { ...item, ...change, status: 'matched' as const } : item,
  )
}

// The items as they are now, once the compute route has worked out `sent`, in the order sent:
// the changed item `key` becomes what was sent, confirmed, and every food sent takes its numbers.
// Anything else stays as it is now, so a food confirmed while the numbers were worked out stays
// confirmed.
export function computed(
  current: SwapItem[],
  sent: SwapItem[],
  numbers: NutritionResult[],
  key: number,
): SwapItem[] {
  const byKey = new Map(sent.map((item, at) => [item.key, { item, numbers: numbers[at] ?? null }]))
  return current.map((item) => {
    const done = byKey.get(item.key)
    if (done === undefined) return item
    if (item.key === key) return { ...done.item, numbers: done.numbers, confirmed: true }
    return { ...item, numbers: done.numbers }
  })
}

export function logItems(items: SwapItem[]): LogItem[] {
  return counted(items).map((item) => ({
    food_id: item.food!.id,
    amount: item.amount!,
    unit: item.unit!,
  }))
}
