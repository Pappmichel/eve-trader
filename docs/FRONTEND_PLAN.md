# Frontend-Plan: Theme und Interaktivität

Stand 2026-10-01. Fasst `THEME_DRAFT.md` und
`docs/FRONTEND_INTERACTIVITY_PLAN.md` zusammen (beide ersetzt). Grundlage:
Branch `ccr-e0ccb567-s04qgg`, React 19, Mantine 9, `@tanstack/react-table` 8,
`@tanstack/react-virtual` 3, React Query 5, Recharts 3.
Aufwand: S = bis ½ Tag, M = 1-2 Tage, L = 3+ Tage.

## Status

| Teil | Stand |
|---|---|
| Theme Schritt 1 (Table, Badge, Button, Tabellenziffern) | umgesetzt, committet |
| Abgeschnittene Spaltenköpfe nach Schritt 1 | offen, siehe Abschnitt A.1 |
| Theme Schritt 2-4 | Entwurf |
| Befunde B1-B3 (Preisverlauf-Endpunkt) | offen, ungeprüft (kein Backend in der Cloud-Session) |
| Interaktivität, alle 14 Punkte | nur Plan |

Nichts davon ist gegen ein echtes Backend geprüft. Die Screenshots zum Theme
entstanden mit gemockter API. Rajdhani wird in der Cloud-Umgebung nicht
geladen (Google Fonts ist geblockt), der Schrifteindruck ist dort also nicht
beurteilbar.

## Leitlinien

- **Mantine bleibt.** Kein Wechsel auf Tailwind/shadcn (siehe Entscheidung
  unten). Anpassung über `theme.ts` (Komponenten-Overrides) und `index.css`.
- **`DataTable` ist der Hebel.** `frontend/src/components/DataTable.tsx` nutzt
  jede Tabellenseite; jede Erweiterung kommt allen Seiten zugute.
- **Opt-in-Props.** Neue Props sind optional, eine Seite ohne sie verhält sich
  wie heute (wie `isError`, `dataUpdatedAt`).
- **Virtualisierung:** Nur ~20 Zeilen sind gemountet. Zeilenbezug über
  `getRowId`, nicht über DOM-Knoten; Scrollen über `virtualizer.scrollToIndex`.
- **Feste Zeilenhöhe** (`rowHeight`, Ellipsis): Zellinhalte müssen in 36 px
  passen.
- **Stabile Zeilen-ID** (`getRowId`) ist Pflicht für Zustand pro Zeile
  (Drawer, Hervorhebung, Inline-Edit), sonst springt er beim Sortieren auf eine
  andere Zeile.
- **Prüfung je Schritt:** `npm run lint`, `npx tsc -b`, `npm test` in
  `frontend/`; bei Backend-Teilen `pytest`; live per Playwright-Wegwerfskript
  mit Screenshots vorher/nachher, Konsole prüfen, Skript danach löschen
  (Regel aus `CLAUDE.md`).
- **Commits ohne Claude-Attribution** (Regel aus `CLAUDE.md`).

## Entscheidung: Mantine behalten

Die Tailwind/shadcn-Vorlagen aus der Recherche (TailAdmin, Shadcn Admin,
Windmill, Admin One) würden ein zweites Styling-System neben Mantine einführen
und alle Seiten betreffen. Der generische Mantine-Look lässt sich über
Theme-Overrides beheben. MUI (Material-Look schwer anzupassen, DataGrid
teilweise kostenpflichtig), Chakra, Ant Design und Radix Themes bringen keinen
Vorteil, der den Umbau rechtfertigt. Optional ausleihen: einzelne Ideen aus
`CSS-sci-fi-ui` für Panel-Rahmen. Nicht verwenden: ARWES (nicht mehr gepflegt),
SCIFICN/UI (setzt shadcn/Tailwind voraus).

---

# Teil A: Theme

Ziel: das bestehende "Trade-Terminal"-Theme stimmiger machen, ohne Palette
(`COLORS`), Schriften oder Dark-Only zu ändern.

## A.1 Schritt 1 (umgesetzt) und offener Rest

Eingebaut in `frontend/src/theme.ts` und `frontend/src/index.css`:
Tabellenköpfe als Rajdhani-Versalien mit Buchstabenabstand und Trennlinien,
eckigere Badges, fette Rajdhani-Schrift bei Buttons, Tabellenziffern mit
gleicher Breite (`font-variant-numeric: tabular-nums`).

**Offen:** Versalien sind breiter. Bei den festen Spaltenbreiten
(`DataTable`, `colgroup`) werden Köpfe stärker abgeschnitten, z. B. "DAYS UNTIL
AU…" und "TREND (3D VS 3…". Lösung, eine von zwei:
1. Schriftgröße der Köpfe von 13 auf 12 px und `letterSpacing` auf 0.04em.
2. Breiten der betroffenen Spalten in den Seiten anheben (Shortlist:
   "Days Until Auto-…", "Trend").
Empfehlung: erst 1, dann mit echter Rajdhani-Schrift im Browser prüfen, da die
Schrift schmaler ist als die Ersatzschrift in den Screenshots.

## A.2 Schritt 2: Tabs und NavLink

```ts
Tabs: { styles: {
  tab: { fontFamily: 'Rajdhani, sans-serif', letterSpacing: '0.04em', color: COLORS.textDim,
         '&[data-active]': { color: COLORS.accent, borderColor: COLORS.accent } },
  list: { borderColor: COLORS.border } } },
NavLink: { styles: {
  root: { borderRadius: 4, '&[data-active]': { background: 'rgba(53, 208, 186, 0.10)', color: COLORS.accent } },
  label: { fontFamily: 'Rajdhani, sans-serif', letterSpacing: '0.03em' } } },
```

## A.3 Schritt 3: Karten, Paper, Eingabefelder

```ts
Paper: { defaultProps: { radius: 'sm', withBorder: true },
  styles: { root: { background: COLORS.surface, borderColor: COLORS.border } } },
Card: { defaultProps: { radius: 'sm', withBorder: true, padding: 'md' },
  styles: { root: {
    background: `linear-gradient(180deg, ${COLORS.surface2} 0%, ${COLORS.surface} 100%)`,
    borderColor: COLORS.border, transition: 'border-color 120ms ease',
    '&:hover': { borderColor: COLORS.accent } } } },
TextInput:   { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
NumberInput: { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
Select:      { styles: { input: { background: COLORS.bg, borderColor: COLORS.border } } },
```

## A.4 Schritt 4: Panel-Dekor und Scrollbars (`index.css`)

```css
.et-panel { position: relative; border: 1px solid #24313F; border-radius: 4px;
  background: linear-gradient(180deg, #182230 0%, #121922 100%); }
.et-panel::before, .et-panel::after { content: ''; position: absolute; width: 10px; height: 10px;
  border: 1px solid #35D0BA; opacity: .7; pointer-events: none; }
.et-panel::before { top: -1px; left: -1px; border-right: 0; border-bottom: 0; }
.et-panel::after { bottom: -1px; right: -1px; border-left: 0; border-top: 0; }
* { scrollbar-width: thin; scrollbar-color: #24313F #0B0F14; }
```
`.et-panel` nur auf Landing-Karten und Seitenköpfen setzen.

## A.5 Hinweise

- Ob verschachtelte Selektoren (`'&:hover'`, `'&[data-active]'`) in
  Mantine-9-`styles` greifen, ist ungeprüft. Falls nicht: Regeln nach
  `index.css` über `.mantine-Card-root:hover` und `[data-active]`.
- Keine neuen Schriften, keine Palettenänderung, keine Animation über den
  120-ms-Rahmenübergang hinaus.
- Reihenfolge: A.1-Rest → A.2 → A.3 → A.4. Je Schritt Screenshots
  vorher/nachher von Landing und Shortlist.

---

# Teil B: Interaktivität

## B.0 Befunde zuerst klären (Phase 0)

Alle drei betreffen `GET /api/trading/history/{type_id}` in
`eve_trader/api/routers/trading.py`. Sparklines, Drawer und Charts bauen
darauf auf. Alle ungeprüft, da kein Backend.

**B1. Zwei Regionen werden gemischt (wahrscheinlicher Fehler).** Der Endpunkt
filtert nur auf `type_id`. `goonmetrics_history` enthält Reihen für
`jita_region_id` und `reference_region_id`. Die Antwort ist nach Datum
sortiert, `PriceHistory.tsx` zeichnet daraus eine Linie. Erwartung: Zickzack
zwischen zwei Preisniveaus. Behebung: nach Region trennen, im Frontend zwei
Linien.

**B2. Liest die ganze Tabelle.** `storage.read_table("goonmetrics_history")`,
Filter erst in Pandas. `storage.read_goonmetrics_history_for_types` filtert in
SQL und existiert schon (für `do_shortlist_trends`).

**B3. Nicht auf den Tenant beschränkt.** Die Auswahlliste `/history/type-ids`
ist seit T3-03 auf eigene Items beschränkt, `/history/{type_id}` liefert für
jede geratene ID Daten aus dem geteilten Cache. Behebung: gegen
`goonmetrics_history_type_ids_for_tenant()` prüfen, sonst 404.

## B.1 Zeilen-Detail per Klick (Drawer) · M

Machbarkeit gut, erster Wurf ohne Backend. Heute kein Click-Handler auf
`Table.Tr`, Drawer gibt es noch nicht (Modals schon).
- `DataTable`: `onRowClick?: (row) => void`, `activeRowId?: string`
  (`data-active`, `cursor: pointer` nur wenn gesetzt). Klicks auf Buttons,
  Inputs, Links ignorieren (`closest('button, input, a, [role=button]')`).
- Neue `components/RowDetailDrawer.tsx` (Mantine `Drawer`, rechts ~420 px,
  mobil volle Breite). Offene Zeile in der URL (`?item=<type_id>`).
- Erste Seite Trading Shortlist: Jita-Preis, Importkosten, Landed Cost,
  Netto-Verkauf, Gewinn/Einheit, Marge, Volumen, Ø Tagesvolumen, Mini-Verlauf
  (nach B.0). Aktionen nur über bestehende Endpunkte; eine Watchlist gibt es
  nicht. Stattdessen Link "In Price History öffnen" (dafür muss
  `PriceHistory.tsx` die Auswahl aus der URL lesen, S).
- Danach Build Candidates, Market Status, Stock Targets (je S).

## B.2 Sparklines · M

Machbarkeit mittel. 50 Zeilen mit je einem Request wären zu viel, jeder
liest heute die ganze Tabelle (B2).
- Backend: `GET /api/trading/history/sparklines`, liefert für die
  Shortlist-Items dieses Tenants je Region die letzten ~28 Tage
  (`{type_id: {"jita": [...], "ref": [...]}}`), über
  `read_goonmetrics_history_for_types`. Reine Lese-Abfrage, darf im Router
  stehen; bei Aggregation (z. B. Marge) ein `do_*`.
- Frontend: `components/Sparkline.tsx` als Inline-SVG (`<polyline>`, ~80×24 px),
  kein Recharts pro Zeile. Farbe nach Richtung, Tooltip über `meta.cellTitle`.
  Eine Query pro Seite (`['trading','sparklines']`).

## B.3 Filter-Chips · S bis M

- Shortlist (S): Klick auf Status-Badge setzt `setSelDecisions([decision])`,
  zweiter Klick setzt zurück; Badge als `UnstyledButton` mit `aria-pressed`.
- Generisch (M): `columnFilters` von tanstack-table, Spalten über
  `meta: { filterValue }`; Chip-Leiste mit "x" und "Alle zurücksetzen".
  `getFilteredRowModel` ist eingebunden.
- Badge-Handler mit `stopPropagation()`, damit er den Drawer (B.1) nicht
  öffnet.

## B.4 Kopierbare Zellen · S

- `useClipboard` aus `@mantine/hooks`. Spalten mit `meta: { copyable: true }`
  zeigen beim Hover ein Kopier-Icon (nicht die ganze Zelle klickbar).
- Rohwert (`cell.getValue()`), kurzes "Kopiert" im Icon statt Toast.
- `navigator.clipboard` braucht HTTPS oder `localhost`; bei
  `!window.isSecureContext` Icon ausblenden.

## B.5 Lade- und Erfolgszustände · M

- `components/JobProgress.tsx` (Mantine `Progress`) aus dem Status von
  `useBackgroundJob` (`batch/total_batches`, sonst unbestimmt), im
  Tool-Layout unter dem Header.
- Geänderte Zeilen kurz hervorheben: `DataTable` merkt sich per `useRef`
  Vorwerte je `getRowId`, nur für Spalten mit `meta.trackChanges`, opt-in und
  nur auf Seiten mit unter ~2.000 Zeilen (Candidate Universe hat 45k+).

## B.6 Gespeicherte Ansichten · M (lokal), M-L (Server)

- Stufe 1 (empfohlen, lokal): Ansicht = `{name, sorting, columnVisibility,
  columnOrder, columnFilters, globalFilter}` unter `datatable:<id>:views`,
  Menü "Ansichten" neben "Columns". Seitenfilter außerhalb von `DataTable`
  (Shortlist-`MultiSelect`s) entweder nach `columnFilters` überführen (B.3
  generisch) oder per `extraViewState` anbinden. `try/catch` wie bisher.
- Stufe 2 (nur bei Bedarf): Server-Speicherung braucht eine neue
  per-Tenant-Tabelle mit RLS. Eine neue Schema-Datei muss laut `CLAUDE.md`
  in `deploy/deploy.sh`, `deploy/README.md`, `README.md` und `.cursor/start.sh`
  eingetragen werden.

## B.7 Inline-Bearbeitung · S

`EditableNumberCell` in `StockTargets.tsx` (Issue #16) hat schon das Muster
(lokaler Entwurf, Haken, `PATCH /api/production/stock-targets/{type_id}`).
Nach `components/EditableCell.tsx` heben, DoctrineDetail (`TargetEditor`)
darauf umstellen. Enter speichert, Escape verwirft, **kein** automatisches
Speichern bei Blur (bewusst, siehe Shortlist-Cap-Kommentar). Weitere
Kandidaten nur mit PATCH-Endpunkt (manuelle Bestände, Listed Quantities,
Logistik-Kategorien). Settings-Seiten bleiben Formulare
(`validate_config_overrides` prüft das ganze Objekt).

## B.8 Hover-Karten · S (niedrige Priorität)

`HoverCard` auf der Namensspalte, ~400 ms Verzögerung, Inhalt nur aus
Zeilen- und Sparkline-Daten (keine Abfrage pro Hover), auf Touch aus
(`(pointer: coarse)`). Überschneidet sich mit B.1; lohnt sich danach nur für
sehr dichte Tabellen.

## B.9 Tastatur und Spotlight-Aktionen · M + M

- Tabelle: fokussierbarer Container, `activeIndex`, Pfeile/Enter/Home/End,
  `virtualizer.scrollToIndex`, `aria-activedescendant` (Zeilen-IDs aus
  `getRowId`). `/` fokussiert den Filter über `useHotkeys` (mit
  `tagsToIgnore`).
- Spotlight (`QuickNav.tsx`, bisher nur Navigation): zweite Gruppe "Aktionen"
  ("Refresh Shortlist", "Reconcile Trades", "Sync ESI") mit derselben
  Grant-Filterung (`TOOL_KEYS`). Aktionen über dieselben Hooks wie die Buttons
  (`useAction`, `useTradingPipelineJob`), damit Toasts, Invalidierung und
  Job-Lock gleich bleiben. Aktionen der Stufe `live` (ESI/Goonmetrics) mit
  Bestätigungsdialog.

## B.10 Interaktive Charts · M

Erst nach B1. `PriceHistory.tsx`: Recharts `<Brush>`, Schnellwahl 7/30/90 Tage
(`SegmentedControl`), zwei Linien für Jita und Referenzregion, Mehrfachauswahl
(`useQueries`, auf Index 100 normiert, maximal ~5 Items). "Klick auf
Datenpunkt springt zur Tabelle" entfällt (Seite hat keine Tabelle); stattdessen
Link aus Shortlist/Drawer (B.1). Portfolio-Verlauf (`/api/portfolio/history`
nimmt `days`) bekommt denselben Zeitraumwähler.

## B.11 Dashboard-Kacheln · M bis L

Landing-Karten bekommen eine optionale KPI-Zeile, nur wenn der Grant da ist
(`gateStatus.tools`). Trading: vorhandener `/kpis`. Portfolio: Total Wealth aus
`/overview` (löst beim ersten Aufruf des Tages einen Snapshot aus, nicht in ein
Polling hängen). Production: `/market-status` und `/plan` sind zu teuer, daher
neuer schlanker `GET /api/production/kpis` (nur zählen). Jede Kachel lädt
unabhängig, Skeleton statt Spinner.

## B.12 Automatisches Aktualisieren · S, Nutzen gering

Der Scheduler ist absichtlich aus (`CLAUDE.md`), Daten ändern sich praktisch
nur durch eigene Aktionen, die ihre Queries schon invalidieren (`useAction`).
Kein globales `refetchInterval`. Stattdessen prüfen, ob `useBackgroundJob` nach
Jobende überall invalidiert, und `refetchOnWindowFocus` nur für ausgewählte
Queries einschalten. Hinweisleiste "Neue Daten verfügbar" erst sinnvoll, wenn
der Scheduler wieder läuft.

## B.13 Drag & Drop · M (Spalten), L (Prioritäten)

Spalten: `columnOrder` von tanstack-table, Ziehen per `@dnd-kit/*`
(~15 kB gz, einzige mögliche neue Abhängigkeit) oder ohne Abhängigkeit mit
Hoch/Runter-Pfeilen im "Columns"-Menü. Speichern wie B.6. Prioritäten
(Stock-Targets): `stock_targets` hat keine Reihenfolge-Spalte, bräuchte
`sort_order`, Migration, PATCH-Feld und Anpassung aller Leser. Zurückstellen,
bis klar ist, was die Reihenfolge steuern soll.

## B.14 Benachrichtigungszentrale · M

42 direkte `notifications.show`-Aufrufe. Zentraler Wrapper `src/notify.ts`
(reicht an Mantine weiter, schreibt in einen kleinen Store über
`useSyncExternalStore`), Aufrufe einmal umstellen, oxlint
`no-restricted-imports` gegen Rückfälle. Glocke im Header mit ungelesen-Zähler
und den letzten ~50 Einträgen, Speicher `sessionStorage`. Fehler bleiben
zusätzlich als Toast sichtbar. Ob Erfolge nur noch in der Zentrale landen, ist
offen.

---

# Reihenfolge (gesamt)

| Phase | Inhalt | Grund |
|---|---|---|
| 0 | A.1-Rest (Spaltenköpfe) · B1-B3 prüfen und beheben | Fehler vor neuen Funktionen; Charts und Drawer bauen darauf auf |
| 1 | Theme A.2 und A.3 (Tabs, NavLink, Karten, Eingabefelder) · `DataTable`: `onRowClick`/`activeRowId`, Tastatur, Kopier-Icon | Fundament für B.1, B.4, B.9 |
| 2 | Drawer Shortlist (B.1) · Status-Chips Shortlist (B.3) · Spotlight-Aktionen (B.9) | sichtbarster Effekt, kein Schema |
| 3 | Sparkline-Endpunkt und Komponente (B.2) · Charts (B.10) · Theme A.4 | braucht Phase 0 |
| 4 | Gespeicherte Ansichten lokal (B.6) · generische Spaltenfilter · Spaltenreihenfolge im Menü | baut auf Phase 1 |
| 5 | Notification-Zentrale (B.14) · Job-Fortschritt und Änderungs-Hervorhebung (B.5) | unabhängig |
| 6 | Dashboard-Kacheln mit Production-`/kpis` (B.11) | neuer Endpunkt |
| später | Inline-Edit verallgemeinern (B.7), Hover-Karten (B.8), Auto-Refresh (B.12), Drag & Drop (B.13) | geringer Zusatznutzen oder Schema nötig |

Neue Abhängigkeiten: keine zwingend (optional `@dnd-kit/*` für B.13).
