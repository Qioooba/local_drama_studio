/**
 * Command identifier and idempotency-key helpers shared by the generated client
 * and every hand-written client module.
 *
 * `crypto.randomUUID` is only exposed in a secure browser context. The product is
 * documented to run over plain HTTP on a LAN address (README `LAN_SERVICE`), where
 * `crypto.getRandomValues` exists but `randomUUID` does not, so a bare
 * `crypto.randomUUID()` throws a TypeError before any request is sent. Everything
 * that needs a command id must go through this module.
 *
 * Two semantics are deliberately kept apart:
 *
 * - `newCommandId()` mints a fresh v4 id for one user action: operation ids,
 *   correlation ids, ordinary identifiers. It is never a security token.
 * - `operationIdempotencyKey(scope, request)` binds an `Idempotency-Key` to the
 *   caller's user operation and to the normalised request instead of to a single
 *   network attempt. A timeout retry with the same scope and payload reuses the
 *   key; changing the payload rotates it; `completeOperation(scope)` retires it so
 *   the next deliberate action is a new operation rather than a replay.
 */

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UUID_BYTE_LENGTH = 16;

const hex = (bytes: Uint8Array): string => Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");

function randomBytes(length: number): Uint8Array {
  const webCrypto = (globalThis as { crypto?: Crypto }).crypto;
  if (webCrypto && typeof webCrypto.getRandomValues === "function") {
    const bytes = new Uint8Array(length);
    try {
      webCrypto.getRandomValues(bytes);
      return bytes;
    } catch {
      /* fall through to the explicit failure below */
    }
  }
  throw new Error("当前浏览器不提供安全随机数能力（crypto.getRandomValues），无法生成操作 ID；请使用 HTTPS 或现代浏览器访问本服务。");
}

function formatUuidV4(bytes: Uint8Array): string {
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = hex(bytes);
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

/** RFC 4122 v4 id. Prefers the platform generator, otherwise builds one from `crypto.getRandomValues`. */
function generateCommandId(): string {
  const webCrypto = (globalThis as { crypto?: Crypto }).crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") return webCrypto.randomUUID();
  return formatUuidV4(randomBytes(UUID_BYTE_LENGTH));
}

function canonical(value: unknown, seen: Set<object>): string {
  if (value === null || typeof value === "number" || typeof value === "boolean") return JSON.stringify(value) ?? "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "undefined") return "null";
  if (typeof value === "function") return '"[function]"';
  if (Array.isArray(value)) return `[${value.map((item) => canonical(item, seen)).join(",")}]`;
  if (typeof value === "object") {
    if (seen.has(value)) return '"[circular]"';
    seen.add(value);
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => typeof item !== "undefined")
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item, seen)}`);
    seen.delete(value);
    return `{${entries.join(",")}}`;
  }
  return "null";
}

/** Canonical JSON with sorted object keys, so structurally equal payloads serialise identically. */
export function canonicalRequest(value: unknown): string {
  return canonical(value, new Set<object>());
}

/** A fresh id for exactly one user action. Never reuse it as a security token. */
export function newCommandId(): string {
  return generateCommandId();
}

/** True when the value has the v4 UUID shape the backend accepts as a command identifier. */
export function isCommandId(value: string): boolean {
  return UUID_PATTERN.test(value);
}

const operationRegistry = new Map<string, { signature: string; id: string }>();

/**
 * Idempotency key bound to the caller's user operation rather than to a single
 * network attempt. Retrying with the same `scope` and normalised `request` reuses
 * the key; changing the request rotates it.
 */
export function operationIdempotencyKey(scope: string, request: unknown): string {
  const signature = canonicalRequest(request);
  const existing = operationRegistry.get(scope);
  if (existing && existing.signature === signature) return existing.id;
  const id = generateCommandId();
  operationRegistry.set(scope, { signature, id });
  return id;
}

/** Retire a finished operation so the next action cannot silently replay the previous request. */
export function completeOperation(scope: string): void {
  operationRegistry.delete(scope);
}

function stableWords(value: string): [number, number, number, number] {
  let first = 0x811c9dc5;
  let second = 0x01000193;
  let third = 0x9e3779b9;
  let fourth = 0x85ebca6b;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second + code + index, 0x85ebca6b);
    third = Math.imul(third ^ (code + (index << 8)), 0xc2b2ae35);
    fourth = Math.imul(fourth + (code << 3) + index, 0x27d4eb2f);
  }
  return [first >>> 0, second >>> 0, third >>> 0, fourth >>> 0];
}

/**
 * Deterministic idempotency key for callers that have no operation session of
 * their own. Identical operation + identical normalised request always yields the
 * same key, so a retry cannot duplicate the command, while a changed payload
 * yields a different key.
 */
export function stableIdempotencyKey(operation: string, request: unknown): string {
  const [first, second, third, fourth] = stableWords(`${operation}\u0000${canonicalRequest(request)}`);
  const words = [first, second, third, fourth];
  const bytes = new Uint8Array(UUID_BYTE_LENGTH);
  for (let block = 0; block < 4; block += 1) {
    for (let index = 0; index < 4; index += 1) bytes[block * 4 + index] = (words[block] >>> (24 - index * 8)) & 0xff;
  }
  return formatUuidV4(bytes);
}

/** Test seam: drop all remembered operation keys. */
export function resetCommandIdCache(): void {
  operationRegistry.clear();
}
