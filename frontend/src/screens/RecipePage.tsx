import type { DayKey } from '../api'
import type { State } from '../storage'

export interface RecipePageProps {
  state: State
  day: DayKey | null
  slotId: string | null
}

export function RecipePage(_props: RecipePageProps) {
  return (
    <h1 tabIndex={-1} data-screen-title>
      Recipe
    </h1>
  )
}
