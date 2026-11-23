import React from 'react';
import { createRoot } from 'react-dom/client';
import { Ob019CardView } from '../src/Ob019Card';
import { PageWindow } from '../src/PageWindow';
import '../src/styles.css';
import '../src/responsive.css';
import '../src/machines.css';
import settings from './settings.json';

const scenario = new URLSearchParams(location.search).get('state') || 'normal';
const offline = scenario === 'offline';
const state = scenario === 'running' ? 103 : scenario === 'alarm' ? 105 : scenario === 'finished' ? 107 : 102;
const completed = scenario === 'finished';
const card: any = {
  equipment_id: 'mock-u19', inventory: 'ОБ.019', kind: 'ob019', name: 'Линия резки и зачистки', pc: 'ПК станка', ip: '', service: 'Мост ERP',
  service_version: '1.4.0', bridge_version: '1.4.0', link: offline ? { code: 'port_busy', text: 'Открыт пульт станка на ПК', tone: 'warn' } : { code: 'ok', text: 'На связи', tone: 'ok' },
  reported_at: '2026-11-23T12:30:00+03:00', age_seconds: offline ? 120 : 1, error: '',
  live: {
    state: { code: state, text: state === 103 ? 'Работает' : state === 105 ? 'Авария' : state === 107 ? 'Тираж готов' : 'Ожидание', tone: 'neutral' },
    alarm: { code: state === 105 ? 6 : 0, text: state === 105 ? 'Нет провода' : 'Аварий нет', tone: state === 105 ? 'danger' : 'neutral' },
    mode: { code: 1, text: 'Один цикл' },
    job: { total_set: completed ? 2050 : 100, total_finished: completed ? 2056 : state === 103 ? 37 : 0, batch_set: 20, batch_finished: 0, uph: 0,
      program: 1, order_quantity: 100, order_finished: 0, order_number: 'ГР-0007', product: 'ПВАМ 0,35 · 325 мм', order_running: false },
    wire: { length_mm: 325, strip_front_mm: 5, pull_front_mm: 7, strip_back_mm: 5, pull_back_mm: 7, diameter_mm: 0.79, knife_back_mm: 0.1 },
  },
};
const control = { problem: offline ? 'Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком' : '', state,
  commands: [], offsets: [{ key: 'length_mm', label: 'Длина провода', offset: '0,00', reason: '', updated_by: '', updated_at: null, etk: false }],
  can_edit: true, can_control: true, can_stop: true, length_correction: { value: '0,00', register: 'D54', legacy_offset: '0,00' } };
const run = scenario !== 'running' ? null : { id: 'mock-run', kind: 'cutting', label: 'ГР-0007 · ПВАМ 0,35 · 325 мм', group_id: 'g1', length_mm: '325', job_id: null,
  quantity: 100, write: { status: 'done', status_text: 'Записано', tone: 'ok', error: '' }, verified_at: '2026-11-23T12:30:00+03:00', closed_at: null,
  closed_why: '', machine_count: null, closed_reset: false, created_by: 'Оператор', created_at: '2026-11-23T12:30:00+03:00', stage: 'running', counter_start: 0,
  counter: 37, done: 37, reset: false, state: 103, alarm: 0, total_set: 100 };
const wire = 'ПВАМ 0,35';
const plan = { group_id: 'g1', number: 'ГР-0007', wire, length_mm: '325,00', quantity: 100,
  expected_source: { group_id: 'g1', version: 1, length_mm: '325', plan_hash: 'a'.repeat(64) }, expected_active_run_id: null,
  rows: Object.entries(card.live.wire).map(([key, value]) => ({ key, label: settings.settings.flatMap(g => g.fields).find(f => f.key === key)?.label || key,
    unit: 'мм', nominal: String(value), source: 'ЭТК', offset: null, value: String(value), live: String(value) })),
  job: { total_set: 100, order_quantity: 100, order_number: 'ГР-0007', product: 'PVAM 0.35 L325' }, missing: [], blocking: [], marking: null };
(window as any).__mockPosts = [];
window.fetch = async (input, init) => {
  const url = decodeURIComponent(String(input));
  if (init?.method && init.method !== 'GET') {
    (window as any).__mockPosts.push({ url, body: JSON.parse(String(init.body || '{}')) });
    return new Response(JSON.stringify({ detail: 'Визуальный макет: запись отключена' }), { status: 409, headers: { 'Content-Type': 'application/json' } });
  }
  const result = url.endsWith('/control') ? control : url.endsWith('/run') ? { run, expected_active_run_id: run?.id || null, manual: null, link_ok: !offline, problem: control.problem, can_edit: true, can_control: true }
    : url.includes('/transfer?wire=') ? { ...plan, group_id: null, number: null, quantity: null, job: { ...plan.job, total_set: null, order_quantity: null } }
    : url.includes('/transfer?') ? plan
    : url.endsWith('/transfer') ? { choices: [{ key: 'g1:325', group_id: 'g1', number: 'ГР-0007', project: '708К.', wire, length_mm: '325', quantity: '100' }], wires: [{ label: wire, conductor_mm: '0,64', has_recipe: true }] }
    : url.includes('/proposal') ? { wire, recipe: null, publish: {}, etk: null, values: [] }
    : url.endsWith('/options') ? { equipment: [], wires: [], purposes: [] }
    : settings;
  return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } });
};
createRoot(document.getElementById('root')!).render(<PageWindow label="ОБ.019 — линия резки и зачистки" close={() => {}} width={1100}>
  <section className="panel machine-card"><Ob019CardView card={card} controls header={{ title: 'ОБ.019 — линия резки и зачистки', close: () => {},
    actions: <><button className="secondary small">Ручная работа</button><button className="secondary small">Наладка</button></> }} /></section>
</PageWindow>);


