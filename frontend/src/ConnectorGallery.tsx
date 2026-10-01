import { useEffect, useState } from "react";
import { failureText, techcardsApi, type ConnectorFamily } from "./api";
import { connectorFace, connectorFaceUnits, connectorPicture, connectorSummary, type ConnectorHousing } from "./connectorArt";

/* «База данных → Разъёмы»: все серии, которые схема жгута и эскиз монтажа рисуют своим корпусом (владелец 30.09:
   «собрать более реальную базу разъёмов»). Сверху — проверка своего артикула: какой корпус нарисует ЭТК. */

function Drawing({ housing }: { housing: ConnectorHousing }) {
  const picture = connectorPicture(housing, 150, 90);
  const round = housing.kind === "round" || housing.kind === "barrel";
  const step = round ? 20 : Math.max(12, Math.min(34, 250 / connectorFaceUnits(housing)));
  const face = connectorFace(housing, 10, 4, step, null);
  return <>
    <svg viewBox={`0 0 ${picture.w} ${picture.h}`} role="img" aria-label={`Корпус ${housing.title}`} dangerouslySetInnerHTML={{ __html: picture.body }} />
    <div>
      <b>{housing.art}</b>
      <small>{connectorSummary(housing)}</small>
      {housing.conf !== "high"
        ? <small className="conn-warn" title={housing.note || undefined}>нумерация не сверена с чертежом</small>
        : housing.src && <small title={housing.note || undefined}>сверено: {housing.src}</small>}
    </div>
    <div className="conn-face"><svg width={face.w + 20} height={face.h + 8} viewBox={`0 0 ${face.w + 20} ${face.h + 8}`}
      role="img" aria-label="Вид со стороны проводов" dangerouslySetInnerHTML={{ __html: face.body }} /></div>
  </>;
}

export function ConnectorGallery() {
  const [items, setItems] = useState<ConnectorFamily[] | null>(null);
  const [error, setError] = useState("");
  const [article, setArticle] = useState("");
  const [match, setMatch] = useState<{ article: string; housing: ConnectorFamily["housing"] | null } | null>(null);

  useEffect(() => {
    techcardsApi.connectorFamilies().then((result) => setItems(result.items))
      .catch((reason) => { setItems([]); setError(failureText(reason, "База разъёмов не загрузилась")); });
  }, []);

  async function check() {
    const value = article.trim();
    if (!value) { setMatch(null); return; }
    try {
      setMatch({ article: value, housing: (await techcardsApi.connectorFamilies(value)).match });
    } catch (reason) { setError(failureText(reason, "Артикул не проверен")); }
  }

  if (items === null) return <div className="empty">Загрузка…</div>;
  return <div className="conn-base">
    <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void check(); }}>
      <input value={article} placeholder="Артикул корпуса, например GHR-04V-S" aria-label="Артикул корпуса" onChange={(event) => setArticle(event.target.value)} />
      <button className="secondary small" type="submit">Проверить артикул</button>
      <span className="muted">{items.length} серий · вид со стороны проводов, контакт 1 — красная метка</span>
    </form>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {match && (match.housing
      ? <div className="conn-gallery"><div className="conn-card"><Drawing housing={match.housing} /></div></div>
      : <div className="warning-inline">«{match.article}» — серия не распознана: на схеме будет только название.</div>)}
    <div className="conn-gallery">{items.map((item) =>
      <div className="conn-card" key={item.family}><Drawing housing={item.housing} /></div>)}
    </div>
  </div>;
}
