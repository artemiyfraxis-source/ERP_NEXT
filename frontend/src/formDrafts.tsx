import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/** Неотправленные формы в памяти оболочки, по пользователю (аудит F07): истёкшая сессия, вход другого сотрудника на
 *  общем ПК или уход в другой раздел не стирают введённую пачку. Только память вкладки — ни браузерного хранилища,
 *  ни сервера: перезагрузка страницы черновик не возвращает. Другой пользователь чужих черновиков не получает;
 *  «Выйти» стирает черновики вышедшего; форма стирает свой черновик после подтверждённой записи. */
export class DraftStore {
  private readonly byUser = new Map<string, Map<string, unknown>>();

  get<T>(userId: string, key: string): T | undefined {
    return this.byUser.get(userId)?.get(key) as T | undefined;
  }

  set(userId: string, key: string, value: unknown) {
    let drafts = this.byUser.get(userId);
    if (!drafts) {
      drafts = new Map();
      this.byUser.set(userId, drafts);
    }
    drafts.set(key, value);
  }

  delete(userId: string, key: string) {
    this.byUser.get(userId)?.delete(key);
  }

  clearUser(userId: string) {
    this.byUser.delete(userId);
  }
}

const DraftContext = createContext<{ store: DraftStore; userId: string } | null>(null);

export function DraftProvider({ store, userId, children }: { store: DraftStore; userId: string; children: ReactNode }) {
  const value = useMemo(() => ({ store, userId }), [store, userId]);
  return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>;
}

export interface FormDraft<T> {
  /** Черновик этого пользователя на момент открытия формы (undefined — нет). */
  initial: T | undefined;
  /** Запомнить состояние формы; null — стереть (форма пуста или запись подтверждена). */
  save: (value: T | null) => void;
}

/** Черновик формы `key` текущего пользователя. Вне оболочки (тест одной страницы) — ничего не хранит. */
export function useFormDraft<T>(key: string): FormDraft<T> {
  const context = useContext(DraftContext);
  const [initial] = useState<T | undefined>(() => context?.store.get<T>(context.userId, key));
  return useMemo(() => ({
    initial,
    save: (value: T | null) => {
      if (!context) return;
      if (value === null) context.store.delete(context.userId, key);
      else context.store.set(context.userId, key, value);
    },
  }), [context, initial, key]);
}
