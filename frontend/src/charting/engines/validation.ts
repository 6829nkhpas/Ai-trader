// Feature: professional-charting-suite
//
// Pure parameter-validation utilities shared by every charting engine.
//
// These helpers reject non-numeric, wrong-type, and out-of-range values and,
// on failure, identify the offending parameter by name so the UI can highlight
// it and present a message while the caller retains its last valid state.
//
// Used by:
//  - chart-type params (range 1..999,999)                       Req 1.6
//  - indicator params (period 1..5,000, BB multiplier 0.1..10.0) Req 2.3, 2.5
//  - strategy params                                            Req 8.6
//  - footprint tick size (> 0)                                  Req 6.9
//  - volume-profile rows (1..1000) and percent (1..100)         Req 7.2
//
// Everything here is pure and side-effect-free so it is a direct PBT target.

import type { NumericRange, ValidationResult } from '../types';

/**
 * Validate a single value against a numeric range.
 *
 * Rejection cases (each returns `ok: false` with `errorParam` set to
 * `paramName`):
 *  - the value is not of type `number` (string, boolean, null, undefined,
 *    object, bigint, symbol, etc.)
 *  - the value is `NaN` or non-finite (`Infinity`/`-Infinity`)
 *  - the range requires an integer and the value is not an integer
 *  - the value is below `range.min` or above `range.max`
 *
 * On success the validated `number` is returned unchanged.
 *
 * @param value     the candidate value of unknown type
 * @param range     the inclusive bounds (and integer constraint) to enforce
 * @param paramName the parameter name reported back when validation fails
 */
export function validateNumeric(
  value: unknown,
  range: NumericRange,
  paramName: string,
): ValidationResult<number> {
  // Reject wrong types up front. `typeof NaN === 'number'`, so the finiteness
  // check below handles NaN/Infinity separately.
  if (typeof value !== 'number') {
    return {
      ok: false,
      errorParam: paramName,
      message: `${paramName} must be a number`,
    };
  }

  if (!Number.isFinite(value)) {
    return {
      ok: false,
      errorParam: paramName,
      message: `${paramName} must be a finite number`,
    };
  }

  if (range.integer && !Number.isInteger(value)) {
    return {
      ok: false,
      errorParam: paramName,
      message: `${paramName} must be an integer`,
    };
  }

  if (value < range.min || value > range.max) {
    return {
      ok: false,
      errorParam: paramName,
      message: `${paramName} must be between ${range.min} and ${range.max}`,
    };
  }

  return { ok: true, value };
}

/**
 * Validate a bag of parameters against a spec of per-parameter ranges.
 *
 * Every key present in `spec` must have a value in `params` that passes
 * `validateNumeric`. Validation runs in the spec's key order and short-circuits
 * on the first failure, returning that parameter's name so the UI can identify
 * exactly which input was rejected. A missing parameter (the key is absent from
 * `params`) is treated as the wrong type (`undefined`) and rejected.
 *
 * On success a new object containing only the validated, spec-declared numeric
 * parameters is returned; extra keys in `params` that are not in the spec are
 * ignored.
 *
 * @param params the candidate parameter values, keyed by parameter name
 * @param spec   the valid numeric range for each required parameter
 */
export function validateParams(
  params: Record<string, unknown>,
  spec: Record<string, NumericRange>,
): ValidationResult<Record<string, number>> {
  const validated: Record<string, number> = {};

  for (const paramName of Object.keys(spec)) {
    const result = validateNumeric(params[paramName], spec[paramName], paramName);
    if (!result.ok) {
      return result;
    }
    validated[paramName] = result.value;
  }

  return { ok: true, value: validated };
}
