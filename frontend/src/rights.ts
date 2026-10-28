import { useSyncExternalStore } from "react";
import type { Principal } from "./api";
import { canAct } from "./navigation";

/* Права «Сканирование документов» (scan) и «Печать документов» (print) нужны глубоко в экранах, куда принципал не
   доходит пропсами. App кладёт сюда вошедшего человека; пока никого не положили (тесты отдельных экранов) — кнопки
   видны, а запрет всё равно держит сервер. */
let current: Principal | null = null;
const listeners = new Set<() => void>();

export function setRightsPrincipal(principal: Principal | null) {
  current = principal;
  listeners.forEach((listener) => listener());
}

const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const snapshot = () => current;

/** Может ли вошедший действовать в разделе (по умолчанию «edit»): суперадмин/админ — всегда. */
export function useCanAct(section: string): boolean {
  const principal = useSyncExternalStore(subscribe, snapshot, snapshot);
  return principal ? canAct(principal, section) : true;
}

export const useCanScan = () => useCanAct("scan");
export const useCanPrint = () => useCanAct("print");
