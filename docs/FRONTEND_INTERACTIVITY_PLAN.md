# Frontend-Interaktivität: Machbarkeit und technischer Plan

Stand 2026-10-01. Reiner Plan, nichts davon ist umgesetzt. Grundlage ist der
aktuelle Code auf Branch `ccr-e0ccb567-s04qgg` (React 19, Mantine 9,
`@tanstack/react-table` 8, `@tanstack/react-virtual` 3, React Query 5,
Recharts 3). Aufwand: S = bis ½ Tag, M = 1-2 Tage, L = 3+ Tage.

## 0. Querschnitt: `DataTable` ist der Hebel

Fast alle Punkte landen in `frontend/src/components/DataTable.tsx`. Jede
Tabellenseite nutzt sie, also bekommt jede Seite neue Fähigkeiten auf einmal,
so wie bei Sortierung, Filter, Spaltenwahl und Export (Issue #15).

Randbedingungen, die jeder Ausbau einhalten muss:

- **Virtualisierung:** Nur ~20 Zeilen sind gemountet. Alles mit Zeilenbezug
  (aktive Zeile, Hervorhebung, Tastatur) muss über die Zeilen-ID laufen, nicht
  über DOM-Knoten, und zum Scrollen `virtualizer.scrollToIndex` nutzen.
- **Feste Zeilenhöhe** (`rowHeight`, Ellipsis): Keine Zelle darf höher werden.
  Sparklines und Icons müssen in 36 px passen.
- **Stabile Zeilen-ID:** Wo neue Funktionen Zustand pro Zeile halten (Drawer,
  Hervorhebung, Inline-Edit), braucht die Seite `getRowId`. Sonst springt der
  Zustand beim Sortieren auf eine andere Zeile (bekannte Fehlerklasse, siehe
  Kommentar in `DataTable.tsx` und `StockTargets.tsx`).
- **Opt-in:** Neue Props bleiben optional. Eine Seite, die sie nicht setzt,
  verhält sich wie heute (gleiche Regel wie `isError`, `dataUpdatedAt`).
- **Tests:** `components/DataTable.test.tsx` erweitern. Jede neue Prop bekommt
  einen Test für "gesetzt" und "nicht gesetzt".

---

## 1. Zeilen-Detail per Klick (Drawer)

**Machbarkeit:** gut. Kein Backend nötig für den ersten Wurf.

**Ist:** `Table.Tr` hat keinen Click-Handler. Drawer gibt es in der App noch
nicht, Modals schon (`@mantine/modals`, z. B. Mail-Compose).

**Umsetzung:**
- `DataTable` bekommt `onRowClick?: (row: T) => void` und
  `activeRowId?: string`. Die aktive Zeile wird über `data-active` markiert
  und per CSS hervorgehoben. `cursor: pointer` nur wenn `onRowClick` gesetzt.
- Klicks auf interaktive Elemente in Zellen (Buttons, Inputs, Links) dürfen
  den Drawer nicht öffnen: im Handler `e.target.closest('button, input, a,
  [role=button]')` prüfen.
- Neue Komponente `components/RowDetailDrawer.tsx` als dünner Wrapper um
  Mantine `Drawer` (rechts, ~420 px, auf Mobile volle Breite).
- Zustand der offenen Zeile in der URL (`?item=<type_id>`), damit Zurück und
  Link-Teilen funktionieren. `useSearchParams` ist schon im Einsatz.
- **Erste Seite: Trading Shortlist.** Inhalt nur aus Daten, die die Zeile
  schon hat: Jita-Preis, Importkosten, Landed Cost, Netto-Verkauf, Gewinn pro
  Einheit, Marge, Volumen, Ø Tagesvolumen. Dazu Mini-Preisverlauf aus dem
  bestehenden `GET /api/trading/history/{type_id}` (aber siehe Befund A).
- **Aktionen im Drawer:** nur bestehende Endpunkte. Eine "Watchlist" gibt es
  im Backend nicht; den Punkt aus dem Vorschlag streiche ich bzw. ersetze ihn
  durch "In Price History öffnen" (Link mit `type_id`, braucht dass
  `PriceHistory.tsx` die Auswahl aus der URL liest, Aufwand S).
- Danach übertragbar auf Build Candidates, Market Status, Stock Targets.

**Aufwand:** M (Shortlist), je weitere Seite S.

## 2. Sparklines in Tabellen

**Machbarkeit:** mittel. Braucht einen neuen Batch-Endpunkt.

**Ist:** Die Daten liegen in `goonmetrics_history` (geteilter Cache, zwei
Regionen: `jita_region_id` und `reference_region_id`). Es gibt nur einen
Endpunkt pro Item. 50 Zeilen = 50 Requests, und jeder davon liest heute die
**ganze** Tabelle (`storage.read_table("goonmetrics_history")` in
`get_price_history`).

**Umsetzung Backend:**
- Neuer GET `GET /api/trading/history/sparklines` ohne Parameter: liefert für
  die Items der Shortlist dieses Tenants je Region eine kompakte Reihe
  (`{type_id: {"jita": [p1..pn], "ref": [...]}}`, letzte ~28 Tage, nur
  `avg_price`).
- Liest über `storage.read_goonmetrics_history_for_types` (existiert schon,
  gefiltert), Item-Liste aus der Shortlist des Tenants. Damit gibt es kein
  Leck wie in T3-03 behoben (fremde Items aus dem geteilten Cache).
- Reine Lese-Abfrage ohne Entscheidungslogik, laut `CLAUDE.md` darf sie direkt
  im Router stehen. Wenn Aggregation dazukommt (z. B. Marge statt Preis),
  gehört sie in ein `do_*`.

**Umsetzung Frontend:**
- Eigene `components/Sparkline.tsx` als reines Inline-SVG (`<polyline>`), ca.
  80×24 px. **Nicht Recharts:** ein `ResponsiveContainer` pro Zeile kostet bei
  Virtualisierung und Scrollen spürbar, und die Interaktion braucht es nicht.
- Farbe nach Richtung (Akzent steigend, Danger fallend), Tooltip über
  `meta.cellTitle` (Start/Ende/Δ %).
- Eine Query pro Seite (`['trading','sparklines']`), Zellen lesen daraus.

**Hinweis zur Spalte "Trend (3d vs 30d)":** In meinen Screenshots war sie leer,
weil die Mock-Daten `{}` lieferten. Ob sie in echt leer ist, hängt davon ab, ob
Goonmetrics-Historie gesammelt wurde. Das ist kein Befund, nur ungeprüft.

**Aufwand:** M.

## 3. Filter als anklickbare Chips

**Machbarkeit:** gut, rein Frontend.

**Ist:** Shortlist hat eigene Filter (`MultiSelect` für Status, Kategorie,
Meta) im Seitencode. `DataTable` kennt nur den globalen Textfilter.

**Umsetzung, zwei Ebenen:**
- **Seitenebene (schnell):** In der Shortlist ruft ein Klick auf einen
  Status-Badge `setSelDecisions([decision])` auf, ein zweiter Klick setzt
  zurück. Badge als `<UnstyledButton>` mit `aria-pressed`. Gleiches Muster für
  die Kategorie-Spalte.
- **Generisch (DataTable):** `columnFilters` von tanstack-table aktivieren.
  Spalten markieren sich über `meta: { filterValue?: (row) => string }` als
  filterbar. Eine Chip-Leiste über der Tabelle zeigt aktive Filter mit "x" und
  "Alle zurücksetzen". `getFilteredRowModel` ist schon eingebunden.
- Konflikt mit Punkt 1: Klick auf Badge filtert, Klick auf den Rest der Zeile
  öffnet den Drawer. Der Badge-Handler ruft `stopPropagation()`.

**Aufwand:** S (Shortlist), M (generisch).

## 4. Kopierbare Zellen

**Machbarkeit:** gut, mit einer Einschränkung.

**Umsetzung:**
- `useClipboard` aus `@mantine/hooks` (schon Abhängigkeit).
- Spalten mit `meta: { copyable: true }` zeigen beim Hover ein kleines
  Kopier-Icon rechts in der Zelle. **Nicht** die ganze Zelle klickbar machen,
  sonst kollidiert es mit Zeilenklick (Punkt 1) und Textmarkierung.
- Kopiert wird der Rohwert (`cell.getValue()`), nicht der formatierte Text,
  also `1234567` statt `1.234.567 ISK`. Bei Itemnamen der Name. Option
  `copyFormat` für Fälle, in denen das Spiel ein anderes Format will.
- Kurzes "Kopiert"-Feedback im Icon (2 s), kein Toast (würde bei häufigem
  Kopieren nerven).
- **Einschränkung:** `navigator.clipboard` braucht HTTPS oder `localhost`.
  Läuft eine Installation über reines HTTP im LAN, fällt das still aus. Dann
  Icon ausblenden (`window.isSecureContext` prüfen).

**Aufwand:** S.

## 5. Lade- und Erfolgszustände sichtbarer machen

**Machbarkeit:** gut. Grundlagen existieren.

**Ist:** Skeletons in `DataTable`. Hintergrundjobs (`useBackgroundJob`)
pollen schon (`refetchInterval` bei `status === 'running'`) und liefern
`progress` mit `batch`/`total_batches`; heute nur als Text in einer Toast.

**Umsetzung:**
- **Fortschrittsbalken:** Neue `components/JobProgress.tsx` (Mantine
  `Progress`), gespeist aus demselben Status wie `formatBackgroundProgress`.
  `batch/total_batches` → Prozent, sonst unbestimmter (animierter) Balken.
  Platz: oben im jeweiligen Tool-Layout unter dem Header, nicht nur in der
  Toast.
- **Geänderte Zeilen hervorheben:** `DataTable` merkt sich per `useRef` die
  vorherigen Werte je `getRowId` (nur wenn gesetzt). Nach einem Refetch
  bekommen geänderte Zeilen ~2 s lang `data-changed` (CSS-Fade). Vergleich nur
  auf ausgewählten Spalten (`meta.trackChanges`), sonst ist jede Zeile
  "geändert", sobald sich ein Zeitstempel bewegt.
- Bei 45k Zeilen (Candidate Universe) ist ein vollständiger Vergleich je
  Refetch zu teuer. Deshalb opt-in, und nur auf Seiten mit < ~2.000 Zeilen.

**Aufwand:** M.

## 6. Gespeicherte Ansichten

**Machbarkeit:** gut (lokal), mittel (serverseitig).

**Ist:** Spaltensichtbarkeit wird pro `tableId` in `localStorage`
gespeichert (`datatable:<id>:columns`). Sortierung, Filter, Spaltenreihenfolge
werden nicht gespeichert.

**Umsetzung Stufe 1 (lokal, empfohlen):**
- Ansicht = `{ name, sorting, columnVisibility, columnOrder, columnFilters,
  globalFilter }`, gespeichert unter `datatable:<id>:views`.
- Menü "Ansichten" in der Toolbar neben "Columns": speichern, laden,
  umbenennen, löschen, "Standard".
- Seitenfilter außerhalb von `DataTable` (Shortlist-`MultiSelect`s) sind nicht
  automatisch dabei. Entweder diese Filter in `columnFilters` überführen
  (passt zu Punkt 3 generisch) oder die Seite übergibt ihren Zustand per
  `extraViewState`/`onApplyView`.
- Gleiche `try/catch`-Regel wie heute (privater Modus, Speicher voll).

**Stufe 2 (serverseitig, nur bei Bedarf):** Ansichten pro Charakter über
Geräte hinweg. Braucht eine neue per-Tenant-Tabelle mit RLS. Laut `CLAUDE.md`
muss eine neue Schema-Datei in `deploy/deploy.sh`, `deploy/README.md`,
`README.md` und `.cursor/start.sh` eingetragen werden. Erst angehen, wenn
Stufe 1 tatsächlich genutzt wird.

**Aufwand:** M (Stufe 1), M-L (Stufe 2).

## 7. Inline-Bearbeitung

**Machbarkeit:** gut, Muster existiert bereits.

**Ist:** Stock Targets haben schon `EditableNumberCell` (Issue #16): lokaler
Entwurf, Haken zum Speichern, `PATCH /api/production/stock-targets/{type_id}`.
DoctrineDetail hat dasselbe Muster (`TargetEditor`).

**Umsetzung:**
- `EditableNumberCell` aus `StockTargets.tsx` nach
  `components/EditableCell.tsx` heben (plus Text-Variante), ohne Verhalten zu
  ändern. DoctrineDetail auf dieselbe Komponente umstellen.
- Bedienung ergänzen: Enter speichert, Escape verwirft, Speichern bei Blur
  **nicht** automatisch (bewusste Entscheidung im Shortlist-Cap-Kommentar:
  ungewollte Saves pro Tastendruck waren ein echter Bug).
- Weitere Kandidaten nur dort, wo es einen PATCH-Endpunkt gibt: manuelle
  Bestände, manuelle Listed-Quantities, Logistik-Kategorien. Einstellungen
  (Settings-Seiten) bleiben Formulare, weil `validate_config_overrides` das
  ganze Objekt prüft und Einzelfeld-Saves dort keinen Vorteil bringen.
- `getRowId` ist Pflicht für jede Tabelle mit editierbaren Zellen.

**Aufwand:** S (Extraktion), je weitere Seite S.

## 8. Hover-Karten mit Kontext

**Machbarkeit:** gut, aber sparsam einsetzen.

**Umsetzung:**
- Mantine `HoverCard` auf der Item-Namen-Spalte, Öffnen mit ~400 ms
  Verzögerung (sonst flackert es beim Scrollen über die Tabelle).
- Inhalt nur aus vorhandenen Zeilendaten plus Sparkline-Daten aus Punkt 2,
  **keine** eigene Abfrage pro Hover (sonst Request-Sturm beim Überfahren).
- Auf Touch-Geräten deaktivieren (`(pointer: coarse)`, gleiche Abfrage wie in
  `index.css`); dort übernimmt der Drawer aus Punkt 1.
- Überschneidung mit Punkt 1: Hover-Karte zeigt die Kurzfassung, Drawer die
  Langfassung. Wenn Punkt 1 kommt, lohnt sich 8 nur noch für sehr dichte
  Tabellen. Daher niedrige Priorität.

**Aufwand:** S.

## 9. Tastaturbedienung und Spotlight-Aktionen

**Machbarkeit:** gut. Hotkeys und Spotlight sind schon da.

**Ist:** `QuickNav.tsx` nutzt `@mantine/spotlight` (`mod + K`) nur zur
Navigation, mit Filter nach Tool-Grants. Sortierbare Spaltenköpfe sind schon
per Tastatur erreichbar (Issue #61).

**Umsetzung Tabelle:**
- `DataTable` bekommt einen fokussierbaren Container (`tabIndex=0`) und einen
  `activeIndex`. Pfeil hoch/runter verschiebt, `virtualizer.scrollToIndex`
  hält die Zeile sichtbar, Enter ruft `onRowClick`, Home/End springen.
- `aria-activedescendant` auf dem Container, damit Screenreader die aktive
  Zeile ansagen (Zeilen brauchen dafür stabile `id`s aus `getRowId`).
- `/` fokussiert den Tabellenfilter, über `useHotkeys` aus `@mantine/hooks`.
  Muss ignoriert werden, wenn der Fokus in einem Eingabefeld steht
  (`useHotkeys` hat dafür `tagsToIgnore`).

**Umsetzung Spotlight-Aktionen:**
- Zweite Gruppe "Aktionen" neben "Seiten", z. B. "Refresh Shortlist",
  "Reconcile Trades", "Sync ESI".
- Gleiche Grant-Filterung wie bei den Seiten (`TOOL_KEYS`).
- Aktionen laufen über dieselben Hooks wie die Buttons (`useAction`,
  `useTradingPipelineJob`), damit Toasts, Invalidierung und Job-Lock identisch
  sind. Das heißt: die Hooks müssen in `QuickNav` aufgerufen werden, also
  außerhalb der Tool-Layouts. Machbar, weil sie nur `useQueryClient`
  brauchen.
- Aktionen der Stufe `live` (ESI/Goonmetrics) mit Bestätigungsdialog, weil
  ein versehentliches Enter sonst einen langen Job startet.

**Aufwand:** M (Tabelle), M (Spotlight-Aktionen).

## 10. Interaktive Charts

**Machbarkeit:** gut, aber zuerst Befund A beheben.

**Ist:** `PriceHistory.tsx` zeigt eine `LineChart` mit einem Item, Auswahl
per `Select`.

**Umsetzung:**
- **Zoom/Zeitraum:** Recharts `<Brush>` (eingebaut) plus Schnellwahl
  7/30/90 Tage über `SegmentedControl`.
- **Regionen getrennt:** zwei Linien Jita vs. Referenzregion (siehe Befund A).
- **Mehrere Items vergleichen:** `MultiSelect` statt `Select`, `useQueries`
  pro Item, normiert auf Index 100 (sonst sind ein 5-ISK- und ein 5-Mrd-Item
  auf einer Achse unlesbar). Maximal ~5 Items.
- **Klick auf Datenpunkt → Tabelle:** In Price History gibt es keine
  Tabelle zum Hinspringen. Sinnvoller ist umgekehrt: aus Shortlist/Drawer per
  Link mit `type_id` hierher (siehe Punkt 1). Den ursprünglichen Vorschlag
  ändere ich so ab.
- Portfolio-Verlauf (`/api/portfolio/history?days=`) bekommt denselben
  Zeitraumwähler; der Endpunkt nimmt `days` schon.

**Aufwand:** M.

## 11. Dashboard-Startseite mit Live-Kacheln

**Machbarkeit:** mittel. Daten teilweise vorhanden.

**Ist:** Landing zeigt statische `ToolCard`s, gefiltert nach Grants.
Vorhanden: `GET /api/trading/kpis` (Shortlist-Anzahl, Importkandidaten,
eigene Sell-Orders, neue Empfehlungen), `GET /api/portfolio/overview`,
`GET /api/production/jobs`, `GET /api/production/market-status`.

**Umsetzung:**
- Jede Karte bekommt optional eine KPI-Zeile, die nur geladen wird, wenn der
  Grant da ist (sonst 403). Grants stehen schon in `gateStatus.tools`.
- Trading: vorhandener `/kpis`. Portfolio: Total Wealth aus `/overview`.
  Production: Anzahl laufender Jobs und fehlende Stock-Targets.
- **Achtung Kosten:** `GET /portfolio/overview` macht einen Snapshot beim
  ersten Aufruf des Tages (Lazy-Fallback, siehe `CLAUDE.md` Scheduler). Das
  auf der Landing-Seite auszulösen ist in Ordnung, aber nicht in einem
  Polling-Intervall.
- **Production-KPIs:** `/market-status` und `/plan` sind teure Berechnungen.
  Für die Kachel einen schlanken `GET /api/production/kpis` bauen, der nur
  zählt (Muster wie Trading `/kpis`).
- Kacheln mit Skeleton statt Spinner; jede Kachel lädt unabhängig, damit ein
  langsamer Endpunkt die Seite nicht blockiert.

**Aufwand:** M-L (mit neuem Production-Endpunkt).

## 12. Automatisches Aktualisieren mit Hinweis

**Machbarkeit:** technisch einfach, **Nutzen derzeit gering**.

**Begründung:** Der Scheduler ist absichtlich aus (`CLAUDE.md`: "scheduler
bleibt offline"). Daten ändern sich praktisch nur durch eigene Aktionen, und
die invalidieren ihre Queries schon (`useAction` → `invalidateQueries`).
Periodisches Polling würde fast immer dieselben Daten laden und bei
`/portfolio/overview` sogar Arbeit auslösen.

**Empfohlene Variante:**
- Kein globales `refetchInterval`.
- Nach Abschluss eines Hintergrundjobs gezielt invalidieren (prüfen, ob
  `useBackgroundJob` das überall tut).
- Für Daten, die sich doch extern ändern (ESI-Syncs anderer Tabs/Geräte):
  `refetchOnWindowFocus` nur für ausgewählte Queries einschalten (global ist
  es aus, siehe `main.tsx`).
- Hinweisleiste "Neue Daten verfügbar – laden" nur, wenn ein Refetch Daten
  liefert, die sich vom angezeigten Stand unterscheiden. Erst sinnvoll, wenn
  der Scheduler wieder läuft.

**Aufwand:** S (Variante), Rest zurückstellen.

## 13. Drag & Drop

**Machbarkeit:** Spalten gut, Prioritäten teuer.

**Spaltenreihenfolge:**
- tanstack-table hat `columnOrder` eingebaut. Ziehen in den Spaltenköpfen mit
  `@dnd-kit/core` + `@dnd-kit/sortable` (neue Abhängigkeit, ~15 kB gz) oder
  ohne Bibliothek per HTML5-Drag-API (geht, aber schlecht auf Touch).
- Alternative ohne neue Abhängigkeit: Hoch/Runter-Pfeile im bestehenden
  "Columns"-Menü. Weniger elegant, aber zugänglicher.
- Speichern wie Spaltensichtbarkeit (`localStorage`) bzw. als Teil von Punkt 6.

**Prioritäten, z. B. Stock-Targets:**
- `stock_targets` hat keine Reihenfolge-Spalte. Bräuchte `sort_order` in der
  Tabelle, Migration, PATCH-Feld und Anpassung aller Leser.
- Prüfen, ob eine Reihenfolge fachlich etwas bedeutet. Heute sortiert die
  Engine nach eigenen Kriterien; eine manuelle Reihenfolge, die nichts
  steuert, wäre nur Kosmetik.
- Empfehlung: zurückstellen, bis klar ist, wofür die Reihenfolge gebraucht
  wird.

**Aufwand:** M (Spalten), L (Prioritäten mit Schema).

## 14. Benachrichtigungszentrale

**Machbarkeit:** gut, rein Frontend.

**Ist:** 42 direkte `notifications.show`-Aufrufe, Toasts verschwinden
(außer `autoClose: false` z. B. beim SDE-Hinweis).

**Umsetzung:**
- `notifications.show` nicht 42-mal ersetzen, sondern einmal zentral
  abfangen: eigener `notify()`-Wrapper in `src/notify.ts`, der an Mantine
  weiterreicht und den Eintrag in einen kleinen Store schreibt
  (`useSyncExternalStore`, keine neue Abhängigkeit). Danach die Aufrufe per
  Suchen/Ersetzen umstellen; ein Lint-Check (oxlint `no-restricted-imports`
  für `notifications` außerhalb von `notify.ts`) verhindert Rückfälle.
- Glocke im Header (`ToolHeader` und Landing) mit ungelesen-Zähler,
  Dropdown mit den letzten ~50 Einträgen (Titel, Zeit, Farbe), "alle gelesen".
- Speicher: `sessionStorage` (pro Tab). Über Neuladen hinaus oder geräteweit
  wäre wieder eine Server-Tabelle nötig; nicht nötig für den Zweck.
- Fehler bleiben zusätzlich als Toast sichtbar, Erfolge könnten optional nur
  noch in der Zentrale landen (weniger Ablenkung). Entscheidung offen.

**Aufwand:** M.

---

## Befunde nebenbei (vor Punkt 1, 2 und 10 klären)

**A. Price History mischt zwei Regionen (wahrscheinlicher Fehler).**
`GET /api/trading/history/{type_id}` filtert nur auf `type_id`.
`goonmetrics_history` enthält aber Reihen für `jita_region_id` und
`reference_region_id` (siehe `history_backtest.py`). Die Antwort ist nach
Datum sortiert, also wechseln sich beide Regionen ab, und `PriceHistory.tsx`
zeichnet daraus **eine** Linie. Erwartung: Zickzack zwischen zwei
Preisniveaus. Nicht live geprüft (kein Backend hier). Vor jeder Chart-Arbeit
verifizieren und beheben.

**B. Derselbe Endpunkt liest die ganze Tabelle.**
`get_price_history` nutzt `storage.read_table("goonmetrics_history")` und
filtert in Pandas. `read_goonmetrics_history_for_types` existiert und filtert
in SQL (wurde für `do_shortlist_trends` genau aus diesem Grund eingeführt).

**C. Derselbe Endpunkt ist nicht auf den Tenant beschränkt.**
T3-03 hat die Auswahlliste (`/history/type-ids`) auf die eigenen Items
beschränkt, aber `/history/{type_id}` liefert für jede beliebige `type_id`
die Historie aus dem geteilten Cache. Wer eine ID rät, sieht, ob ein anderer
Tenant das Item recherchiert hat. Gleiche Leckklasse wie T3-03, kleiner, weil
man raten muss. Bei Bedarf auf `goonmetrics_history_type_ids_for_tenant()`
prüfen und sonst 404.

## Reihenfolge

| Phase | Inhalt | Warum zuerst |
|---|---|---|
| 0 | Befunde A-C | Charts und Drawer bauen darauf auf |
| 1 | `DataTable`: `onRowClick`/`activeRowId`, Tastatur, Kopier-Icon | Fundament für 1, 4, 9 |
| 2 | Drawer Shortlist, Status-Chips Shortlist, Spotlight-Aktionen | sichtbarster Effekt, kein Schema |
| 3 | Sparkline-Endpunkt + Komponente, Charts (Brush, Regionen, Vergleich) | braucht Phase 0 |
| 4 | Gespeicherte Ansichten (lokal), generische Spaltenfilter, Spaltenreihenfolge im Menü | baut auf Phase 1 |
| 5 | Notification-Zentrale, Job-Fortschrittsbalken, Änderungs-Hervorhebung | unabhängig |
| 6 | Dashboard-Kacheln mit Production-`/kpis` | neuer Endpunkt |
| später | Inline-Edit verallgemeinern, Hover-Karten, Auto-Refresh, DnD | geringer Zusatznutzen oder Schema nötig |

## Neue Abhängigkeiten

Keine zwingend. Einzige Option: `@dnd-kit/*` für Spalten-Drag (Punkt 13), mit
Menü-Alternative ohne Abhängigkeit.

## Prüfung je Phase

- `npm run lint`, `npx tsc -b`, `npm test` im Ordner `frontend/`.
- Backend-Teile: `pytest`, Router-Tests in `tests/test_api_routers.py` nach
  dem dort üblichen Muster (Modul-Objekte monkeypatchen).
- Live: Playwright-Wegwerfskript laut `CLAUDE.md`, Screenshots vorher/nachher,
  Konsole prüfen, Skript danach löschen. Echte Endpunkte zusätzlich gegen
  `localhost:8000` prüfen, sobald ein Backend läuft (in dieser Cloud-Session
  gibt es keins, hier nur mit gemockter API).
