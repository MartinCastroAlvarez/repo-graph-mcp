import { Cache } from "./util";

export class Service {
  constructor(private cache: Cache) {}

  warm(): void {
    this.cache.evict();
  }
}

export const makeService = (): Service => new Service(new Cache());
