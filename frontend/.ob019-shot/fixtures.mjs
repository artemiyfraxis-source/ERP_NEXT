import fs from 'node:fs';
const code = fs.readFileSync(new URL('../../backend/app/modules/production/machines.py', import.meta.url), 'utf8').split('OB019_SETTINGS:')[1].split('# Registers ERP never writes')[0];
const defaults = { mode: 1, total_set: 100, batch_set: 20, batch_delay_s: 0.5, order_quantity: 100, order_number: 'ГР-0007', product: 'ПВАМ 0,35 · 325 мм',
  length_mm: 325, strip_front_mm: 5, pull_front_mm: 7, strip_back_mm: 5, pull_back_mm: 7, diameter_mm: 0.79, knife_back_mm: 0.1,
  marking: true, mark_by_step: true, mark_front: '', mark_middle: 'PVAM 0.35', mark_back: '', mark_pos_front_mm: 20, mark_pos_back_mm: 20, mark_step_mm: 100,
  mark_count: 0, mark_font: 2, mark_bold: 1, mark_divider: 4, mark_char_mm: 1.5, mark_head_mm: 200, mark_hold_s: 0.1, mark_roll_speed: 30,
  coil_ratio_min: 1, coil_ratio_max: 1, feed_wheel: false };
const groups = [];
for (const line of code.split('\n')) {
  const heading = line.match(/^    \("([^"]+)", \(/);
  if (heading) groups.push({ title: heading[1], fields: [] });
  for (const m of line.matchAll(/_f\("([^"]+)", [^,]+, "([^"]+)"([^)]*)\)/g)) {
    const [, label, kind, args] = m;
    const key = args.match(/key="([^"]+)"/)?.[1] || null;
    const unit = args.includes('"мм"') ? 'мм' : args.includes('"с"') ? 'с' : args.includes('"шт"') ? 'шт' : '';
    const value = key && key in defaults ? defaults[key] : kind === 'bit0' ? false : kind === 'text15' ? '' : key?.startsWith('speed_') ? 30 : 0;
    groups.at(-1).fields.push({ key, label, kind, unit, value, read: true, ...(kind === 'mode' ? { text: 'Один цикл' } : {}) });
  }
}
fs.writeFileSync(new URL('./settings.json', import.meta.url), JSON.stringify({ inventory: 'ОБ.019', settings: groups }, null, 2));
