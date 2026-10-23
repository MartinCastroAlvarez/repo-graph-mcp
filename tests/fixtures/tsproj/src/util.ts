/** Helpers shared by the app. */

/** Turn a title into a URL slug. */
export function slugify(text: string): string {
  return text.toLowerCase().replace(/\s+/g, "-");
}

export class Store {
  get(key: string): string | undefined {
    return undefined;
  }
}

export class Cache extends Store {
  private size = 0;

  put(key: string, value: string): void {
    this.evict();
    this.size += 1;
  }

  evict(): void {}
}

export const VERSION = "1.0";
