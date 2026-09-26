# Audit 2026-09-25 — Ergebnisse und Umsetzungsstand

Zusammenfassung des erweiterten Sicherheits-/Business-Logic-Audits vom 2026-09-25
(Repo-Stand `f222136`) und der darauf folgenden Umsetzung (2026-09-26,
`f222136..7e75dba`). Der vollständige, vertrauliche Original-Bericht mit allen
Einzelheiten/Agent-Reports liegt außerhalb dieses Repos (siehe unten); dieses
Dokument ist die dauerhafte, ins Repo committete Kurzfassung: was wurde
gefunden, was wurde daraufhin getan — und was bewusst *nicht*.

**Stand:** Alle hier gelisteten Fixes sind auf `main` gemerged (PRs #199–#203)
und auf der Produktion (`evetrader.duckdns.org`, 92.5.11.10) deployed
(`git log` auf dem Server zeigt `7e75dba`). ⚠️ Der laufende `eve-trader`-Prozess
wurde beim letzten Deploy nicht neu gestartet (siehe „Offene Punkte" unten) —
die Codebasis auf der Platte ist aktuell, aber Python lädt Moduländerungen
nicht automatisch nach.

Voller Bericht/Einzelfunde (vertraulich, außerhalb Dropbox):
`/home/pentest/eve_trader_audit/ABSCHLUSSBERICHT.md` +
`findings/consolidated_findings_register.md`.

---

## Status-Legende

| Symbol | Bedeutung |
|---|---|
| ✅ | Gefixt und deployed |
| 🔍 | Untersucht, als **kein** Bug bestätigt |
| 📝 | Dokumentiert, bewusst **nicht** geändert |
| ⏸ | Zurückgestellt (Feature-Scope, kein Bug) |
| ⛔ | Bewusst übersprungen (geringe Priorität/Aufwand) |
| ❓ | Offen — Entscheidung/Input von dir nötig |

---

## Tier 1 — kritisch, echter ISK-/Sicherheits-Impact

| ID | Befund | Status | Commit/PR |
|---|---|---|---|
| T1-01 (PB-03) | Realized Trades zog bei journal-gematchten Verkäufen keine Verkaufssteuer ab (~3,37% des Bruttoumsatzes, bei 10% Marge ≈ ein Drittel des ausgewiesenen Profits) | ✅ Echter Steuerabzug via `context_id`-Verknüpfung + `id+1`-Tax-Entry, mit Plausibilitäts-Guard gegen Fehlzuordnung bei zeitgleichen Verkäufen | `ed7d69d`, `4364620`, `6019080` |
| T1-02 | *(ursprünglich: 3 Test-Fehlschläge als frische Regression durch den Audit-Tag-Commit vermutet)* — durch unabhängigen Gegencheck **widerlegt**: falscher Commit identifiziert, tritt nur bei lokaler `config.yaml`-Einstellung auf, CI nie betroffen. Umbenannt zu T3-11 (siehe unten) | 🔍/✅ | siehe T3-11 |
| T1-03 | Station-Trading-Brokergebühr-Default wirkt veraltet (5% vs. live ~3%) | 🔍 Herabgestuft zu T2-10 (Live-Override bereits korrekt) | siehe T2-10 |
| T1-04 | Mehrere ältere ESI-Snapshot-Tabellen (`character_assets`, `corp_assets`, `*_industry_jobs`, `*_blueprints`, `character_sell_orders`, `character_slots`) hatten keinen `tenant_id` im Primärschlüssel — ein Corp, der mehreren Tenants gemeinsam ist, konnte Zeilen kollidieren lassen (live bestätigt: Corp 98370861 in 5 Tenants) | ✅ Alle 8 Tabellen auf `(tenant_id, …)` erweitert | `d33286a`, plus Folge-Fix `a3e2895` (siehe „Prozess-Vorfälle") |
| T1-04 (Nebenfund) | Corp-platzierte Orders sickerten über einen persönlichen Token in den eigenen Sell-Order-Sync eines Charakters | ✅ Gefiltert | `8f27db3` |
| T1-05 | `.env.bak-*` auf dem Produktionsserver world-readable (Session-Secret, OAuth-Secret, DB-Passwort potenziell lesbar) | ✅ Datei entfernt, `.env`/`config.yaml` jetzt `600`; zusätzlich `backup.py` chmod't jetzt explizit statt sich auf den Caller-Umask zu verlassen (2 Runden, siehe unten) | `2034164`, `2a4110e` |
| T1-06 | Produktions-Backups 4 Tage ausgefallen, keine Fehlermeldung im Log | ✅ Root-Cause: `scheduler_enabled` bewusst aus (kein Bug) — von dir bestätigt: „scheduler bleibt offline, backups werden von hand ausgeführt". Erfolgs-Logging ergänzt, Restore-Drill live durchgeführt und dokumentiert | `8960ed2`, `68cbdc6`, `350743f` |

---

## Tier 2 — real, aber engerer/geringerer Impact

| ID | Befund | Status | Commit/PR |
|---|---|---|---|
| T2-01 | Reprocessing-Tab wendete beim Paste-Import die Schrottmetall-Ausbeuteformel (max. ~55%) auch auf Erz/Eis an statt der echten Formel (bis ~90,6%) | ✅ Generischer `type_id → (Familie, is_ice)`-Klassifikator ergänzt, Formel-Wahl pro Zeile korrigiert | PR #203 |
| T2-02 | Production Job-Cost/EIV wurde bei ESI-Preisausfall für den **gesamten Plan** still auf 0 gesetzt statt „unbekannt" | ✅ `adjusted_prices_available`-Flag auf `plan_production`/`plan_special_order`; Warnbanner auf der Special-Orders-Seite (wo Job Cost tatsächlich angezeigt wird) | PR #203 |
| T2-03 | Trading-Haircut (0,9463) setzt stillschweigend Accounting V voraus | 📝 Dokumentiert als bekannte Annahme, nicht geändert | `dbc53a4` |
| T2-04 | Ore & Minerals' `refining_tax_rate` defaultet auf 0 | 📝 Dokumentiert als bewusst unkonfigurierter Platzhalter | `dbc53a4` |
| T2-05 (PB-05) | 3x-Buy-Lookback-Erweiterung praktisch wirkungslos (ESI liefert nur 30 Tage Historie) | 🔍 Bestätigt harmlos (sichere Richtung: unter-, nicht übertreibt) | von dir bestätigt, keine Code-Änderung nötig |
| T2-06 (PB-04) | Marktiefe/Slippage nicht modelliert, betrifft jetzt auch Station Trading | ⏸ Bewusst zurückgestellt als Feature (Entscheidung vom 2026-08-29), kein Bug | unverändert |
| T2-07 | Unbekannte `/api/*`-Pfade lieferten HTTP 200 (SPA-Fallback) statt 404 — live von Internet-Scannern aktiv angefragt | ✅ Ausgeschlossen, inkl. Edge-Case `/api`/`/api/` ohne Trailing-Segment | `d5e76a7`, `ae89d48` |
| T2-08 | Kein getesteter Backup-Restore-Pfad | ✅ Live-Restore-Drill durchgeführt (Schema/Daten/Anwendungsebene, Wegwerf-DB) und dokumentiert | `68cbdc6` |
| T2-09 | Systematisch deutschsprachiger UI-Text im Sorting-Tool | ✅ „Wareneingang"→„Intake" (UI-Text) und der interne `"markt"`-API-Wert →`"market_listing"` umgestellt; bewusst **nicht** angefasst: interne Backend-Kommentare, die den Begriff repo-weit als etablierten Fachbegriff nutzen | PR #201 |
| T2-10 (war T1-03) | Station-Trading-Brokergebühr | 🔍 Bestätigt **kein Bug** — Live-Override (0,0147) bereits korrekt, entspricht dem bestätigten Jita-Satz | `9401d6c` |
| T2-11 | CSP-Header fehlte komplett in nginx trotz „DONE"-Status in der alten Doku | ✅ Ergänzt, auf Basis echter Frontend-Asset-Analyse (kein externes CDN außer Google Fonts) | `98556d4` |

---

## Tier 3 — niedrige Priorität

| ID | Befund | Status | Commit/PR |
|---|---|---|---|
| T3-01 | Admin `do_set_tool_grants`: N+1 einzelne DB-Writes ohne Transaktion; kein Schutz vor Entfernen des letzten Admin-Grants | ✅ `storage.replace_tool_grants` (eine Transaktion) + Guard gegen „letzten Admin entfernen" | PR #203 |
| T3-02 | Verwaiste `tool_grants`-Zeilen (falscher `tenant_id`) wurden von der Admin-UI ungefiltert gelesen | ✅ Abgleich jetzt auch gegen den aktuellen `tenant_id` des Charakters, nicht nur `character_id` | PR #203 |
| T3-03 | `goonmetrics_history`-Type-ID-Liste zeigte **jedem** Tenant, welche Items **jeder andere** Tenant je recherchiert hat | ✅ Listing auf das eigene Shortlist/Candidate-Universe des anfragenden Tenants beschränkt; der geteilte Preis-Cache selbst bleibt unverändert (echte, legitim geteilte Marktdaten) | PR #203 |
| T3-04 | `jita_buy_broker_fee` doppelt gepflegt (TradingConfig **und** ProductionConfig) | ✅ Zusammengeführt — `ProductionConfig`-Kopie entfernt, jeder Production/Doctrine-Leser liest jetzt `TRADING_CONFIG` direkt | PR #203 |
| T3-05 | `invention.reducible_material_cost` behandelte einen fehlenden Material-Preis als 0 statt „unbekannt" | ✅ Gibt jetzt `None` zurück; `material_savings_per_run`/`net_cost_per_run` werden entsprechend `None`, ohne die unabhängigen `expected_cost_per_*`-Felder zu beeinträchtigen | PR #203 |
| T3-06 | `DataTable.tsx`s `localStorage`-Schreibzugriff ohne try/catch (Crash-Risiko z.B. Safari Private Mode) | ✅ Abgesichert | `521d12e` |
| T3-07 | RealizedTrades: Avg Buy/Sell Price und Avg Margin waren unweighted (nicht mengen-gewichtete) Durchschnitte | ✅ Mengen-gewichtet; `avgMargin` jetzt aus den gewichteten Summen abgeleitet (nicht Durchschnitt von Einzel-Margins) | PR #203 |
| T3-08 | Testabdeckungslücken: Station-Trading-Aktivierung, Doctrine-Asset-Sync, Admin-Allowlist-Routen — jeweils null Coverage | ✅ Tests ergänzt (neue Storage-Test-Datei für Station Trading, Router-Tests für Doctrine-Asset-Sync und alle 5 Admin-Allowlist-Routen) | PR #203 |
| T3-09 | Ein lokaler Test hängt 30s statt sauber ohne Postgres zu skippen | ✅ Fehlender Mock ergänzt | Tier-3-Fix-Runde |
| T3-10 | gitleaks/trufflehog-Versionen für den Secret-Scan ungepinnt | ⛔ Übersprungen — betrifft nur diese Audit-Sandbox, nicht CI/Produktion | — |
| T3-11 (war T1-02) | 3 Tests scheitern lokal bei gesetzter `structure_id` in `config.yaml` — falsch als frische CI-Regression eingestuft, tatsächlich ein älterer, lokal-konfigurationsabhängiger Test-Coverage-Gap | ✅ Fehlender Mock ergänzt (`production_esi_sync.list_capability_characters`) | `7328ea0` |

---

## Sicherheit/Infrastruktur (aus dem ursprünglichen `AUDIT.md`, re-verifiziert)

| ID | Befund | Status |
|---|---|---|
| F-01 | *(on hold)* | 📝 Unverändert, wie gewünscht nicht angefasst |
| F-02 | Security-Header (HSTS etc.) korrekt; CSP fehlte trotzdem — siehe T2-11 | ✅ Siehe T2-11 |
| F-03 | Dropbox-Ordner-Zugriff | ❓ Weiterhin offen, aus dem Repo heraus nicht verifizierbar |
| F-04 | Geld-Spalten müssen `DOUBLE PRECISION` sein | ✅ Bestätigt intakt, auch jede neue Money-Spalte seither (Portfolio, Wallet-Balances, Ore & Minerals) |
| F-05 | Session-Revocation | ✅ Bereits vorher implementiert (DB-Re-Check pro Request, Logout/Removal revoken serverseitig) |
| F-06 | Dependency-Scanning: SAST, npm/Frontend-Audit, Lockfile-Hashes, Action-Pinning | ✅ **Teilweise** — `pip-audit` + Dependabot existierten bereits; `npm audit --omit dev` + SHA-Pinning der GitHub Actions (`checkout`/`setup-python`/`setup-node`) diese Runde ergänzt. ⛔ SAST und Lockfile-Hash-Verifikation weiterhin nicht umgesetzt (bewusst übersprungen, größerer separater Aufwand) |
| F-07 | uvicorn läuft single-worker | ⛔ Übersprungen — Architektur-Entscheidung mit Deploy-Implikationen, sollte separat und bewusst entschieden werden |
| F-08 | rpcbind lauscht auf `0.0.0.0:111` | ✅ Live bestätigt: `inactive`/`masked`, nicht mehr exponiert |
| F-09 | In-Memory-Dicts (OAuth-State, Rate-Limits) ohne Größenbegrenzung | 📝 Bereits gemildert (größenbegrenzt), unverändert diese Runde |
| F-10 | `/api/errors` ohne Login erreichbar | ✅ Bereits effektiv gefixt (Login-Pflicht greift), ein veralteter Code-Kommentar behauptete noch das Gegenteil — nicht separat korrigiert |

---

## Prozess-Vorfälle, die die Umsetzung selbst verursacht hat

Nicht Teil der ursprünglichen Audit-Funde, aber real und hier dokumentiert,
weil sie zeigen, wo diese Art von Schema-/Config-Änderung typischerweise
bricht:

- **CI auf `main` rot nach dem T1-04-Merge** — die PK-Erweiterung übersah
  einen `ON CONFLICT`-Target in einer Doctrine-Migrations-DO-Block
  (`docs/esi_access_schema.sql`) und `sqlite_migration.py`s
  Conflict-Spalten-Liste (letztere fand ein unabhängiger Gegencheck-Agent
  vor mir). Beides ist nur gegen echtes Postgres sichtbar — diese Sandbox
  hat keins, daher liefen die betroffenen Tests hier immer nur „skipped",
  nie „failed". **Gefixt in PR #199.**
- **`deploy.sh` konnte Schema-Dateien nach `git pull` nicht mehr lesen** —
  eine frisch geänderte Schema-Datei erbt beim Checkout den Umask der
  aufrufenden interaktiven Shell (die `umask 077`-Härtung aus T1-05), was
  sie für den `postgres`-User unlesbar machte. **Gefixt in PR #200**
  (explizites `chmod` direkt nach `git pull`, bevor der Schema-Loop läuft).
- **Der zweite Batch-PR (#203) brach beim ersten CI-Lauf** — zwei Bugs in
  meinen eigenen neuen Tests, wieder nur gegen echtes Postgres sichtbar:
  fehlende Attribute in Fake-`_PlanContext`-Stubs, und ein neuer Test, der
  eine geteilte Tabelle ohne aktiven Tenant-Context beschrieb. **Gefixt im
  selben PR**, nachträglicher Commit.

**Lehre für zukünftige Sessions:** Diese Sandbox hat kein lokales Postgres —
jeder Fix, der Schema/RLS/Tenant-Scoping berührt, muss über den echten
CI-Lauf verifiziert werden, nicht nur über den lokalen (der solche Fälle
stillschweigend als „skipped" durchwinkt statt sie als „failed" zu zeigen).

---

## Weitere in dieser Session geklärte Punkte (keine Audit-Funde im engeren Sinn)

- **Git-Tag `v0.2.0-rc1`**: `docs/VERSIONING.md`/`docs/archive/PHASE_H_RELEASE.md`
  behaupteten die Existenz eines Git-Tags — `git tag -l` zeigt keine Tags im
  Repo. Falsche Behauptung entfernt, Paketversion (`0.2.0rc1`, echt, steht in
  `pyproject.toml`) bleibt bestehen. **PR #202.**

---

## Offene Punkte — Entscheidung/Input von dir nötig

1. **Produktions-Service läuft noch auf dem alten Prozess** — der Code auf
   der Platte ist aktuell (`7e75dba`), aber `ExecMainStartTimestamp` zeigt
   noch den Stand vom PR-#200-Deploy (15:35 UTC). Python lädt
   Modul-Änderungen nicht automatisch nach — ein `systemctl restart
   eve-trader` ist nötig, damit PR #201–#203 tatsächlich live wirken.
2. **F-03** (Dropbox-Ordner-Zugriffskontrolle) — außerhalb dieses Repos,
   nicht von hier aus verifizierbar.
3. **F-07** (single-worker uvicorn) — bewusst nicht angegangen, echte
   Architekturentscheidung.
4. **T3-10** (Scan-Tool-Versionspinning) — bewusst übersprungen, betrifft nur
   die Audit-Sandbox.
5. **F-06 Rest** (SAST, Lockfile-Hash-Verifikation) — bewusst zurückgestellt,
   größerer separater Aufwand.
6. **Zweiter Server** (92.5.33.33, „Flex") — nie geprüft, war explizit
   außerhalb des Scopes dieses gesamten Durchgangs.

---

## Referenz: alle Pull Requests dieser Umsetzung

| PR | Titel | Inhalt |
|---|---|---|
| — (direkt auf `main`, vor der Branch-Workflow-Umstellung) | 18 Commits `f222136..350743f` | T1-01, T1-04, T1-05, T1-06, SPA-Fallback, CSP, rpcbind, Station-Trading-Brokergebühr, Backup-Logging/Restore-Drill, DataTable-Fix, Tier-3-Testfixes |
| [#199](https://github.com/Pappmichel/eve-trader/pull/199) | Fix CI red on main | Stale `ON CONFLICT` aus T1-04, `sqlite_migration.py`, SPA-Fallback-Test-Fix |
| [#200](https://github.com/Pappmichel/eve-trader/pull/200) | Fix deploy.sh permissions | Schema-Datei-Lesbarkeit nach `git pull` |
| [#201](https://github.com/Pappmichel/eve-trader/pull/201) | Sorting tool: English | T2-09 |
| [#202](https://github.com/Pappmichel/eve-trader/pull/202) | Remove false git-tag claim | Git-Tag-Doku-Korrektur |
| [#203](https://github.com/Pappmichel/eve-trader/pull/203) | Business-logic audit follow-up | T2-01, T2-02, T3-01, T3-02, T3-03, T3-04, T3-05, T3-07, T3-08, F-06 |
