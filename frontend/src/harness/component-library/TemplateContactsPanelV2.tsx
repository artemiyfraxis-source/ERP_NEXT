import { useState } from "react";
import type { TemplateContentV2 } from "./template-model-v2";
import {
  availableLogicalContactsForViewV2,
  collectContactCoverageDiagnosticsV2,
  contactPointsWithLogicalV2,
  type AvailableLogicalContactV2,
} from "./template-contact-links-v2";
import "./TemplateContactsPanelV2.css";

export interface TemplateContactsPanelV2Props {
  readonly content: TemplateContentV2;
  readonly activeViewId: string;
  readonly onCreateLogicalContact: () => void;
  readonly onPlaceLinkedContact: (logicalContactId: string) => void;
  readonly onAddBundlePort: () => void;
  readonly onNavigateToMissingPoint: (viewId: string, logicalContactId: string) => void;
  readonly onSelectContactPoint?: (pointId: string) => void;
  readonly onSelectBundlePort?: (portId: string) => void;
  readonly preferredLogicalContactId?: string | null;
}

function contactOptionLabel(item: AvailableLogicalContactV2): string {
  const source = item.representedIn.length
    ? item.representedIn.map(representation => representation.viewName).join(", ")
    : "ещё не размещён";
  return `№${item.contact.number} · ${item.contact.name} · ${source}`;
}

export function TemplateContactsPanelV2({
  content,
  activeViewId,
  onCreateLogicalContact,
  onPlaceLinkedContact,
  onAddBundlePort,
  onNavigateToMissingPoint,
  onSelectContactPoint,
  onSelectBundlePort,
  preferredLogicalContactId,
}: TemplateContactsPanelV2Props) {
  const activeView = content.views.find(view => view.id === activeViewId) ?? null;
  const available = availableLogicalContactsForViewV2(content, activeViewId);
  const points = contactPointsWithLogicalV2(content, activeViewId);
  const diagnostics = collectContactCoverageDiagnosticsV2(content);
  const [chosenLogicalContactId, setChosenLogicalContactId] = useState(preferredLogicalContactId ?? available[0]?.contact.id ?? "");
  const selectedLogicalContactId = available.some(item => item.contact.id === chosenLogicalContactId)
    ? chosenLogicalContactId
    : available[0]?.contact.id ?? "";

  if (!activeView) {
    return <section className="template-contacts-v2" role="status" aria-label="Контакты вида"><p>Активный вид не найден.</p></section>;
  }

  return (
    <section className="template-contacts-v2" aria-label={`Контакты вида ${activeView.name}`}>
      <header className="template-contacts-v2-heading">
        <span><strong>Контакты вида</strong><small>{activeView.name}</small></span>
        <span className="template-contacts-v2-count">{points.length}</span>
      </header>

      <div className="template-contacts-v2-actions">
        <button
          type="button"
          onClick={onCreateLogicalContact}
          disabled={activeView.kind !== "e4"}
          aria-label="Создать логический контакт"
          title={activeView.kind === "e4" ? undefined : "Новые логические контакты создаются в виде Э4"}
        >
          Создать контакт
        </button>
        <button type="button" onClick={onAddBundlePort} aria-label="Добавить общий выход пучка">
          + Выход пучка
        </button>
      </div>

      <div className="template-contact-link-v2">
        <label>
          Разместить контакт из другого вида
          <select
            aria-label="Логический контакт для размещения"
            value={selectedLogicalContactId}
            disabled={!available.length}
            onChange={event => setChosenLogicalContactId(event.target.value)}
          >
            {!available.length && <option value="">Все контакты уже размещены</option>}
            {available.map(item => <option key={item.contact.id} value={item.contact.id}>{contactOptionLabel(item)}</option>)}
          </select>
        </label>
        <button
          type="button"
          disabled={!selectedLogicalContactId}
          aria-label="Разместить связанную точку"
          onClick={() => selectedLogicalContactId && onPlaceLinkedContact(selectedLogicalContactId)}
        >
          Разместить связанную точку
        </button>
      </div>

      <div className="template-contact-representations-v2">
        <strong>Точки контактов</strong>
        {points.length ? <ul>{points.map(({ point, contact }) => (
          <li key={point.id}>
            <button
              type="button"
              onClick={() => onSelectContactPoint?.(point.id)}
              aria-label={contact
                ? `Выбрать точку контакта ${contact.number}: ${contact.name}`
                : `Выбрать точку с отсутствующим логическим контактом ${point.logicalContactId}`}
            >
              <span>{contact ? `№${contact.number}` : "?"}</span>
              <strong>{contact?.name ?? "Логический контакт не найден"}</strong>
              <small>{contact?.contactType || "тип не задан"}</small>
            </button>
          </li>
        ))}</ul> : <p>В этом виде ещё нет контактных точек.</p>}
      </div>

      <div className="template-bundle-ports-v2">
        <strong>Общие выходы пучка</strong>
        {activeView.bundlePorts.length ? <ul>{activeView.bundlePorts.map(port => (
          <li key={port.id}>
            <button
              type="button"
              onClick={() => onSelectBundlePort?.(port.id)}
              aria-label={`Выбрать общий выход пучка: ${port.name}`}
            >
              <span aria-hidden="true">◆</span><strong>{port.name}</strong><small>{port.direction}</small>
            </button>
          </li>
        ))}</ul> : <p>Общий выход пучка не задан.</p>}
      </div>

      <section
        className={diagnostics.length ? "template-contact-diagnostics-v2 has-errors" : "template-contact-diagnostics-v2"}
        role="status"
        aria-live="polite"
        aria-label="Диагностика точек подключения"
      >
        <header><strong>Связи между видами</strong><span>{diagnostics.length}</span></header>
        {diagnostics.length ? <ul>{diagnostics.map(diagnostic => (
          <li key={`${diagnostic.viewId}:${diagnostic.logicalContactId}`}>
            <button
              type="button"
              onClick={() => onNavigateToMissingPoint(diagnostic.viewId, diagnostic.logicalContactId)}
              aria-label={`Перейти к отсутствующей точке контакта ${diagnostic.contactNumber} в виде ${diagnostic.viewName}`}
            >
              {diagnostic.message}
            </button>
          </li>
        ))}</ul> : <p>Все контакты представлены в Э4 и чертеже.</p>}
      </section>
    </section>
  );
}
