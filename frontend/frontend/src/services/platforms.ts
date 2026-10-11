// The delivery platforms, named in one place.
//
// A platform has two names and they are not the same string:
//
//  * `id` is the wire name: what the backend sends over the earnings stream and
//    what the database stores. Uber Eats is stored as "UberEats", with no
//    space. Keys into any map of per-platform data use this.
//  * `displayName` is what a partner reads: "Uber Eats", with a space.
//
// Mixing the two is what made the Uber Eats card stop updating: the stream
// sends "UberEats" and this app used to keep its numbers under "Uber Eats", so
// the live value landed on a key nothing rendered and the card stayed frozen at
// its starting figure while the total silently counted Uber twice.
//
// The backend has a matching module, `Backend/platforms.py`. The `id` values in
// the two files are the contract between them and must stay identical.

export interface PlatformInfo {
  /** Wire name: what the stream sends and the database stores. */
  readonly id: string;
  /** What a partner reads. */
  readonly displayName: string;
  readonly color: string;
  readonly icon: string;
  /** Opening figure for the dashboard before any live value arrives. */
  readonly startingEarnings: number;
  /**
   * Demo-mode tick, in ms. Mirrors `tick_seconds` in Backend/platforms.py so
   * the simulated stream behaves like the live one.
   */
  readonly incrementIntervalMs: number;
}

export const PLATFORMS: readonly PlatformInfo[] = [
  {
    id: 'Zomato',
    displayName: 'Zomato',
    color: '#E23744',
    icon: '🛵',
    startingEarnings: 4000,
    incrementIntervalMs: 5000,
  },
  {
    id: 'Swiggy',
    displayName: 'Swiggy',
    color: '#FC8019',
    icon: '📦',
    startingEarnings: 3500,
    incrementIntervalMs: 13000,
  },
  {
    id: 'UberEats',
    displayName: 'Uber Eats',
    color: '#000000',
    icon: '🚗',
    startingEarnings: 1700,
    incrementIntervalMs: 18000,
  },
] as const;

/** How much a demo-mode tick adds. Matches `increment` in Backend/platforms.py. */
export const DEMO_INCREMENT = 100;

/** A comparison key: 'Uber Eats', 'ubereats' and 'UberEats' all match. */
export function platformKey(name: string): string {
  return (name ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

const BY_KEY = new Map(PLATFORMS.map((p) => [platformKey(p.id), p]));

/** The platform a spelling refers to, or undefined when it names none of them. */
export function findPlatform(name: string): PlatformInfo | undefined {
  return BY_KEY.get(platformKey(name));
}

/**
 * The display name for any spelling, falling back to what was given.
 *
 * An unknown platform is shown as it arrived rather than hidden, because the
 * evidence a partner sends may come from an app FairPay does not know yet.
 */
export function platformDisplayName(name: string): string {
  return findPlatform(name)?.displayName ?? name;
}

/** The opening dashboard figures, keyed by wire name. */
export function startingEarnings(): Record<string, number> {
  return Object.fromEntries(PLATFORMS.map((p) => [p.id, p.startingEarnings]));
}
