import { DataTable, type DataColumn } from "./DataTable";
import type { EmployeeCardPersonnel, EmployeeCardSkill } from "./api";
import { formatDate } from "./format";

/** Вкладка «Компетенции»: операции из матрицы допусков — допуск, звёзды, коэффициент — и квалификация из кадровых данных.
 *  Только чтение. Название операции по-русски, код — мелкой серой строкой под ним. */

function coefficientText(value: string) {
  const number = Number(value);
  return Number.isFinite(number) ? String(number).replace(".", ",") : value;
}

const COLUMNS: DataColumn<EmployeeCardSkill>[] = [
  {
    key: "operation", title: "Операция", wrap: true, value: (item) => item.name?.trim() || "операция без названия",
    render: (item) => {
      const name = item.name?.trim();
      return name ? <><strong>{name}</strong><small className="ec-code">{item.operation}</small></> : <span title={item.operation}>операция без названия</span>;
    },
  },
  {
    key: "allowed", title: "Допуск", width: 120, filter: true, value: (item) => item.allowed ? "Допущен" : "Нет допуска",
    render: (item) => <span className={item.allowed ? "tone-ok" : "tone-danger"}>{item.allowed ? "Допущен" : "Нет допуска"}</span>,
  },
  {
    key: "stars", title: "Звёзды", width: 110, numeric: true, value: (item) => item.allowed ? item.stars : "",
    render: (item) => item.allowed ? <span className="ec-stars" aria-label={`${item.stars} из 5`}>{"★".repeat(item.stars) || "—"}</span> : <span className="ec-nodata">—</span>,
  },
  {
    key: "coefficient", title: "Коэффициент", width: 120, numeric: true, value: (item) => item.allowed ? Number(item.coefficient) : "",
    render: (item) => item.allowed ? `×${coefficientText(item.coefficient)}` : <span className="ec-nodata">—</span>,
  },
];

function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  return <div><dt>{label}</dt><dd>{value?.trim() ? value : <span className="ec-nodata">нет данных</span>}</dd></div>;
}

export function EmployeeSkills({ skills, personnel }: { skills: EmployeeCardSkill[]; personnel?: EmployeeCardPersonnel | null }) {
  const sorted = [...skills].sort((a, b) => Number(b.allowed) - Number(a.allowed) || b.stars - a.stars
    || (a.name ?? a.operation).localeCompare(b.name ?? b.operation, "ru"));
  const allowed = skills.filter((item) => item.allowed).length;
  return <div className="lv-page">
    <section className="panel lv-summary" aria-label="Квалификация">
      <h2>Квалификация</h2>
      <dl className="lv-balance">
        <Fact label="Категория" value={personnel?.category} />
        <Fact label="Должность" value={personnel?.position} />
        <Fact label="В должности с" value={personnel?.since ? formatDate(personnel.since) : null} />
        <div><dt>Допущен к операциям</dt><dd>{allowed} из {skills.length}</dd></div>
      </dl>
    </section>
    <section className="panel lv-block" aria-label="Допуски к операциям">
      <h2>Допуски к операциям{skills.length > 0 && <span className="page-count">{skills.length}</span>}</h2>
      {skills.length > 0
        ? <DataTable rows={sorted} columns={COLUMNS} rowKey={(item) => item.operation} storageKey="employee-skills"
          exportName="Компетенции" pageSize={100} searchPlaceholder="Операция" />
        : <p className="empty">Допусков в матрице компетенций нет.</p>}
    </section>
  </div>;
}
