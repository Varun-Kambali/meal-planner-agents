// The types of validators.js, which scripts/generate-validators.ts writes.
import type { ValidateFunction } from 'ajv/dist/2020'

// A file a person loads, in the format of schemas/export.json.
export declare const exported: ValidateFunction
// What this browser saved: the same format, with no plan rules until they are saved.
export declare const stored: ValidateFunction
// A plan-rules file, in the format of schemas/plan-rules.json.
export declare const rulesFile: ValidateFunction
