import { Cat } from './cat.entity';

export interface Finder {
  find(id: string): Cat | undefined;
}

export class CatsService implements Finder {
  find(id: string): Cat | undefined {
    return undefined;
  }

  add(): void {}
}
