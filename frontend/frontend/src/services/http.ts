// The one place that turns a fetch into either data or a thrown error.
//
// This replaces four near-copies of the same "if (!response.ok) read detail,
// else throw" block. Three of them did `err.detail || fallback`, which renders
// "[object Object]" when FastAPI answers a 422 with its list-of-problems shape,
// so a validation error reached the partner as gibberish. `detailOf` below
// handles every shape FastAPI actually sends.

/** Base URL for every call. Set VITE_API_BASE_URL per environment. */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

/** A failed API call, carrying the status so callers can tell 404 from 500. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  /** True when the server could not be reached at all. */
  get isOffline(): boolean {
    return this.status === 0;
  }
}

/** One FastAPI validation problem, as it appears inside a 422 `detail` list. */
interface ValidationProblem {
  msg?: string;
  loc?: (string | number)[];
}

function isValidationProblemList(value: unknown): value is ValidationProblem[] {
  return Array.isArray(value) && value.every((v) => typeof v === 'object' && v !== null);
}

/** The human-readable message inside a FastAPI error body. */
export function detailOf(body: unknown, fallback: string): string {
  if (typeof body === 'string' && body.trim()) return body;
  if (typeof body !== 'object' || body === null) return fallback;

  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;

  // A 422 sends `detail: [{loc, msg, type}, ...]`.
  if (isValidationProblemList(detail)) {
    const messages = detail
      .map((p) => {
        const field = Array.isArray(p.loc) ? p.loc.filter((l) => l !== 'body').join('.') : '';
        return field && p.msg ? `${field}: ${p.msg}` : p.msg;
      })
      .filter((m): m is string => Boolean(m));
    if (messages.length > 0) return messages.join('; ');
  }
  return fallback;
}

/**
 * The response's JSON body, or an ApiError.
 *
 * `fallback` is what the partner sees when the server sent no usable message -
 * so it should say what failed, not "Error".
 */
export async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    // An error body that is not JSON is not itself an error worth reporting;
    // the status and the fallback already say what happened.
    const body = await response.json().catch(() => null);
    throw new ApiError(detailOf(body, fallback), response.status, body);
  }
  try {
    return (await response.json()) as T;
  } catch (cause) {
    throw new ApiError(
      `${fallback} (the server's reply could not be read)`,
      response.status,
      cause,
    );
  }
}

/**
 * `fetch` against the API, resolving to JSON or throwing an ApiError.
 *
 * A network failure becomes an ApiError with status 0 rather than a bare
 * TypeError, so every caller has one error type to handle.
 */
export async function apiFetch<T>(
  path: string,
  options: { fallback: string } & RequestInit,
): Promise<T> {
  const { fallback, ...init } = options;
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch (cause) {
    throw new ApiError(
      `${fallback} (could not reach the FairPay server)`,
      0,
      cause,
    );
  }
  return jsonOrThrow<T>(response, fallback);
}

/** `apiFetch` for a JSON body, so no caller repeats the header and stringify. */
export function apiPost<T>(path: string, body: unknown, fallback: string): Promise<T> {
  return apiFetch<T>(path, {
    fallback,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
