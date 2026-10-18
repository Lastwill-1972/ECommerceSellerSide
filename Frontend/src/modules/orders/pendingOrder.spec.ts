import { describe, it, expect } from 'vitest';
import {
  savePendingOrder,
  readPendingOrder,
  clearPendingOrder,
  normalizeOrderItems,
  scopeStorageKey,
} from './pendingOrder';

const UUID = '12345678-1234-4234-8234-123456789abc';
const ISO = '2024-05-01T00:00:00.000Z';

type StorageLike = {
  getItem: (k: string) => string | null;
  setItem: (k: string, v: string) => void;
  removeItem: (k: string) => void;
};

function createStorage(): StorageLike & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (k) => (map.has(k) ? (map.get(k) as string) : null),
    setItem: (k, v) => {
      map.set(k, v);
    },
    removeItem: (k) => {
      map.delete(k);
    },
  };
}

const scopeA = { user_id: '1', merchant_id: '10', shop_id: '100' };
const scopeB = { user_id: '2', merchant_id: '20', shop_id: '200' };

describe('normalizeOrderItems', () => {
  it('精确保存并读取超过 2^53 的 SKU 与数量', () => {
    const storage = createStorage();
    const bigSku = '9223372036854775807';
    const bigQty = '9007199254740993';

    savePendingOrder(
      {
        key: UUID,
        items: [{ sku_id: bigSku, quantity: bigQty }],
        ...scopeA,
        created_at: ISO,
      },
      storage,
    );

    const raw = storage.map.get(scopeStorageKey(scopeA)) as string;
    expect(raw).toContain(bigSku);
    expect(raw).toContain(bigQty);

    const read = readPendingOrder(scopeA, storage);
    expect(read).not.toBeNull();
    expect(read?.items[0].sku_id).toBe(bigSku);
    expect(read?.items[0].quantity).toBe(bigQty);

    const normalized = normalizeOrderItems([{ sku_id: bigSku, quantity: bigQty }]);
    expect(normalized).toHaveLength(1);
    expect(normalized[0].sku_id).toBe(bigSku);
    expect(normalized[0].quantity).toBe(bigQty);
  });

  it('重复 SKU / 0 / 小数 / 越界 / 空数组 / 超过 100 均拒绝', () => {
    const cases: Array<Array<{ sku_id: unknown; quantity: unknown }>> = [
      [
        { sku_id: '1', quantity: '1' },
        { sku_id: '1', quantity: '2' },
      ],
      [{ sku_id: '1', quantity: '0' }],
      [{ sku_id: '1', quantity: '1.5' }],
      [{ sku_id: '1', quantity: '9223372036854775808' }],
      [{ sku_id: '9223372036854775808', quantity: '1' }],
      [{ sku_id: '-1', quantity: '1' }],
      [],
    ];
    const over = Array.from({ length: 101 }, (_, i) => ({
      sku_id: String(i + 1),
      quantity: '1',
    }));
    cases.push(over);

    for (const items of cases) {
      expect(() => normalizeOrderItems(items as never)).toThrow();
    }
  });
});

describe('savePendingOrder / readPendingOrder', () => {
  it('caller 修改输入 items 不改变 storage 中已保存的 payload', () => {
    const storage = createStorage();
    const items = [{ sku_id: '111', quantity: '2' }];
    savePendingOrder(
      { key: UUID, items, ...scopeA, created_at: ISO },
      storage,
    );
    const rawBefore = storage.map.get(scopeStorageKey(scopeA)) as string;

    items[0].sku_id = '999';
    items[0].quantity = '50';
    items.push({ sku_id: '222', quantity: '3' });

    const rawAfter = storage.map.get(scopeStorageKey(scopeA)) as string;
    expect(rawAfter).toBe(rawBefore);
    const read = readPendingOrder(scopeA, storage);
    expect(read?.items).toHaveLength(1);
    expect(read?.items[0]).toEqual({ sku_id: '111', quantity: '2' });
  });

  it('不同 user / merchant / shop 之间无交叉读取', () => {
    const storage = createStorage();
    savePendingOrder(
      { key: UUID, items: [{ sku_id: '1', quantity: '1' }], ...scopeA, created_at: ISO },
      storage,
    );
    expect(readPendingOrder(scopeB, storage)).toBeNull();
    expect(readPendingOrder({ ...scopeA, user_id: '3' }, storage)).toBeNull();
    expect(readPendingOrder({ ...scopeA, merchant_id: '11' }, storage)).toBeNull();
    expect(readPendingOrder({ ...scopeA, shop_id: '101' }, storage)).toBeNull();
    expect(scopeStorageKey(scopeA)).toBe('seller.pending-order.v1:1:10:100');
  });

  it('scopeA 存储键被注入 scopeB 记录时拒绝读取', () => {
    const storage = createStorage();
    savePendingOrder(
      { key: UUID, items: [{ sku_id: '1', quantity: '1' }], ...scopeB, created_at: ISO },
      storage,
    );
    const keyB = scopeStorageKey(scopeB);
    const rawB = storage.map.get(keyB) as string;
    storage.map.set(scopeStorageKey(scopeA), rawB);

    expect(() => readPendingOrder(scopeA, storage)).toThrow();
    expect(storage.map.get(scopeStorageKey(scopeA))).toBe(rawB);
  });

  it('损坏 JSON 必须提示人工核对且禁止重新下单，并保留原记录', () => {
    const storage = createStorage();
    const key = scopeStorageKey(scopeA);
    const corrupt = '{not valid json';
    storage.map.set(key, corrupt);

    let caught: unknown = null;
    try {
      readPendingOrder(scopeA, storage);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(Error);
    const message = String((caught as Error).message) + String((caught as Error).name);
    expect(/人工|manual/i.test(message)).toBe(true);
    expect(/核对|verify|check/i.test(message)).toBe(true);
    expect(/禁止|forbidden|must not/i.test(message)).toBe(true);
    expect(/重复下单|re-?order|resubmit/i.test(message)).toBe(true);
    expect(storage.map.get(key)).toBe(corrupt);
  });
});

describe('storage 故障', () => {
  it('读写/移除抛错不可静默成功', () => {
    const throwing: StorageLike = {
      getItem: () => {
        throw new Error('get failed');
      },
      setItem: () => {
        throw new Error('set failed');
      },
      removeItem: () => {
        throw new Error('remove failed');
      },
    };

    expect(() =>
      savePendingOrder(
        { key: UUID, items: [{ sku_id: '1', quantity: '1' }], ...scopeA, created_at: ISO },
        throwing,
      ),
    ).toThrow();
    expect(() => readPendingOrder(scopeA, throwing)).toThrow();
    expect(() => clearPendingOrder(scopeA, throwing)).toThrow();
  });
});
