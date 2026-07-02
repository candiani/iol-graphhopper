# Analisi: Integrazione del Traffico in GraphHopper 11.0

> **Data:** luglio 2026  
> **Versione GraphHopper analizzata:** 11.0  
> **Scopo:** Valutare le possibilità di integrare dati di traffico real-time e previsionale in GraphHopper, con particolare attenzione ai servizi TomTom.

---

## Indice

1. [Stato attuale di GraphHopper 11.0](#1-stato-attuale-di-graphhopper-110)
2. [Meccanismi di personalizzazione del routing disponibili](#2-meccanismi-di-personalizzazione-del-routing-disponibili)
3. [Servizi TomTom disponibili per il traffico](#3-servizi-tomtom-disponibili-per-il-traffico)
4. [Approcci di integrazione possibili](#4-approcci-di-integrazione-possibili)
5. [Traffico previsionale: TomTom e GraphHopper](#5-traffico-previsionale-tomtom-e-graphhopper)
6. [Matrice di confronto degli approcci](#6-matrice-di-confronto-degli-approcci)
7. [Raccomandazioni e next steps](#7-raccomandazioni-e-next-steps)

---

## 1. Stato attuale di GraphHopper 11.0

GraphHopper 11.0 è un motore di routing open source basato su dati **OpenStreetMap (OSM)**. Il grafo stradale è costruito a partire da dati statici e le velocità degli archi sono derivate dai tag OSM (`maxspeed`, tipo di strada, ecc.) e da regole specifiche per paese.

### Cosa NON è presente nativamente

- **Nessun supporto per traffico real-time** nella versione open source
- **Nessun supporto per traffico previsionale** (storico orario)
- Nessuna integrazione con provider di dati di traffico (TomTom, HERE, Google, ecc.)
- Le velocità sono statiche (calcolate al momento dell'import del grafo)

### Cosa è presente (rilevante per il traffico)

| Funzionalità | Descrizione |
|---|---|
| `car_average_speed` | Velocità media dell'auto sul segmento (da OSM, statica) |
| `max_speed` | Limite di velocità (da cartelli OSM o regole nazionali) |
| `car_temporal_access` | Restrizioni di accesso condizionali da tag OSM (`access:conditional`) |
| `urban_density` | Classificazione RURAL / RESIDENTIAL / CITY per ogni arco |
| `custom_model` | Sistema di regole per modificare velocità e priorità per richiesta |
| `custom_areas` | Aree geografiche poligonali per applicare regole specifiche |

> **Nota:** `car_temporal_access` gestisce restrizioni temporali già codificate in OSM (es. "corsia riservata ai bus dalle 7 alle 9"), ma **non** è traffico in tempo reale.

---

## 2. Meccanismi di personalizzazione del routing disponibili

### 2.1 Modalità di routing e compatibilità con traffico dinamico

GraphHopper offre tre modalità di routing con comportamenti molto diversi rispetto alla possibilità di integrare dati variabili:

| Modalità | Pre-computazione | Velocità query | Custom Model dinamico | Adatto al traffico |
|---|---|---|---|---|
| **Speed Mode (CH)** | Sì (Contraction Hierarchies) | ~ms | ❌ No | ❌ No |
| **Hybrid Mode (LM)** | Sì (Landmarks) | ~10ms | ✅ Sì (per-request) | ✅ Parzialmente |
| **Flexible Mode** | No | ~100-500ms | ✅ Sì (per-request) | ✅ Sì |

Per qualsiasi integrazione di traffico dinamico **è necessario disabilitare il CH** (`ch.disable=true`) e usare la modalità Hybrid o Flexible. Questo ha un impatto sulle prestazioni di routing.

### 2.2 Custom Model

Il `custom_model` è il principale meccanismo di GraphHopper per modificare il comportamento del routing. Permette di:

- **Modificare la velocità** (`speed`) tramite `multiply_by` o `limit_to`
- **Modificare la priorità** (`priority`) per penalizzare certi archi
- **Applicare regole su aree geografiche** (`areas`, poligoni GeoJSON)
- **Combinare condizioni** su valori encoded (road_class, road_environment, country, ecc.)

**Esempio:** ridurre la velocità su tutti gli archi di una zona congestionata:

```json
{
  "speed": [
    {
      "if": "in_zona_congestione",
      "multiply_by": "0.3"
    }
  ],
  "areas": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "id": "zona_congestione",
        "geometry": {
          "type": "Polygon",
          "coordinates": [[[11.23, 43.77], [11.24, 43.77], [11.24, 43.78], [11.23, 43.78], [11.23, 43.77]]]
        }
      }
    ]
  }
}
```

**Limitazione critica:** le `areas` sono poligoni geografici, non segmenti stradali individuali. Non è possibile, tramite custom model, modificare la velocità di un singolo arco stradale identificato per ID.

### 2.3 Custom Weighting (API Java)

Attraverso la Java API è possibile implementare un `Weighting` personalizzato che calcola il peso di ogni arco dinamicamente a runtime. Questo è l'approccio più flessibile ma richiede sviluppo Java e funziona **solo in Flexible Mode** (no CH, no LM).

```java
// Esempio concettuale
public class TrafficWeighting extends AbstractAdjustedWeighting {
    private final TrafficSpeedCache trafficCache;

    @Override
    public double calcEdgeWeight(EdgeIteratorState edge, boolean reverse) {
        double trafficSpeed = trafficCache.getSpeed(edge.getEdgeKey());
        // usa trafficSpeed per calcolare il peso
    }
}
```

---

## 3. Servizi TomTom disponibili per il traffico

### 3.1 Traffic Flow API (Traffico Real-Time)

**Aggiornamento:** ogni minuto  
**Copertura:** globale (vedere [market coverage](https://developer.tomtom.com/traffic-api/documentation/tomtom-maps/v1/product-information/market-coverage))

#### Flow Segment Data

L'endpoint più utile per l'integrazione con GraphHopper:

```
GET https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/{zoom}/json
    ?key={API_KEY}&point={lat},{lon}
```

**Risposta:**
```json
{
  "frc": "FRC2",
  "currentSpeed": 41,
  "freeFlowSpeed": 70,
  "currentTravelTime": 153,
  "freeFlowTravelTime": 90,
  "confidence": 0.59,
  "roadClosure": false,
  "coordinates": { ... }
}
```

| Campo | Significato |
|---|---|
| `currentSpeed` | Velocità media attuale (km/h) |
| `freeFlowSpeed` | Velocità in condizioni di flusso libero (km/h) |
| `currentTravelTime` | Tempo di percorrenza attuale (secondi) |
| `freeFlowTravelTime` | Tempo di percorrenza in flusso libero (secondi) |
| `confidence` | Affidabilità del dato (0..1) |
| `roadClosure` | Chiusura stradale in atto |

Il rapporto `currentSpeed / freeFlowSpeed` fornisce il **fattore di congestione** (es. 0.59 = percorri il 59% della velocità libera).

#### Traffic Flow Tiles (Raster/Vector)

Tile di traffico per visualizzazione su mappa. Utili per determinare le zone congestionate tramite analisi visiva o programmatica.

### 3.2 Traffic Incidents API (Incidenti Real-Time)

**Aggiornamento:** ogni minuto

Fornisce informazioni su:
- Incidenti stradali (posizione, tipo, gravità)
- Cantieri
- Chiusure stradali
- Code e rallentamenti

Categorie di incidente: `JAM`, `ROAD_WORK`, `ROAD_CLOSURE`, `OTHER`

Formato risposta: tile raster/vettoriali o dati testuali con posizione, lunghezza del ritardo, gravità.

### 3.3 TomTom Routing API (con traffico integrato)

L'API di routing TomTom integra nativamente traffico real-time e storico. I parametri più rilevanti:

| Parametro | Descrizione |
|---|---|
| `traffic=true` | Considera il traffico real-time nel calcolo del percorso |
| `departAt={datetime}` | Routing time-dependent (traffico previsto all'ora di partenza) |
| `arriveAt={datetime}` | Calcolo inverso con orario di arrivo desiderato |
| `computeTravelTimeFor=all` | Restituisce tre valori di tempo di percorrenza |

Con `computeTravelTimeFor=all` la risposta include:
- `noTrafficTravelTimeInSeconds` – tempo senza traffico
- `historicTrafficTravelTimeInSeconds` – tempo con traffico storico (previsionale)
- `liveTrafficIncidentsTravelTimeInSeconds` – tempo con incidenti real-time

> **Importante:** questo routing è un **servizio separato** rispetto a GraphHopper e non è direttamente integrabile come sorgente di dati nel motore di routing di GraphHopper.

### 3.4 Traffico Previsionale TomTom

TomTom non espone un'API standalone per il traffico previsionale (storico per ora del giorno). Il traffico previsionale è integrato internamente nel loro Routing API tramite:
- Il parametro `departAt` che considera i pattern storici di traffico per l'orario specificato
- Il campo `historicTrafficTravelTimeInSeconds` nella risposta

Per accedere ai dati storici di velocità per segmento stradale (necessari per costruire profili predittivi in GraphHopper) sarebbe necessario il prodotto enterprise **TomTom Historical Traffic Analytics**, che non è parte dell'offerta standard della developer API pubblica.

---

## 4. Approcci di integrazione possibili

### Approccio A — Custom Areas con dati Traffic Flow (Real-Time, Bassa Precisione)

**Come funziona:**
1. Periodicamente (es. ogni 2-5 minuti) interrogare la TomTom Traffic Flow API per l'area geografica di interesse
2. Identificare le zone con alta congestione (`currentSpeed / freeFlowSpeed < soglia`)
3. Costruire poligoni GeoJSON attorno alle zone congestionate
4. Includere questi poligoni come `areas` nel `custom_model` della richiesta di routing
5. Ridurre la velocità con `multiply_by` in proporzione alla congestione

**Flusso:**
```
TomTom Flow API → Cluster zone congestionate → GeoJSON areas → custom_model GH → routing
```

**Vantaggi:**
- Nessuna modifica al codice Java di GraphHopper
- Implementabile come middleware/proxy HTTP
- Funziona con la Hybrid Mode (performance accettabile)

**Limitazioni:**
- Precisione geografica limitata ai poligoni (non ai singoli archi stradali)
- Overhead per costruire i poligoni ad ogni richiesta
- Difficile mappare correttamente le zone TomTom alle strade GH

**Complessità di sviluppo:** ★★☆☆☆ (media-bassa)

---

### Approccio B — Custom Weighting Java con cache TomTom (Real-Time, Alta Precisione)

**Come funziona:**
1. Implementare un `CustomWeighting` Java personalizzato in GraphHopper
2. Al momento dell'import del grafo, mappare ogni edge GraphHopper → coordinata → segmento TomTom (tramite Flow Segment Data API)
3. Mantenere in memoria una cache `edgeId → velocità_attuale` aggiornata periodicamente
4. Il weighting personalizzato usa la velocità TomTom invece di quella OSM

**Componenti necessari:**
- **Mapper GH↔TomTom:** costruito durante l'import, risolve ogni edge GH nel corrispondente segmento TomTom
- **Traffic Cache:** HashMap in memoria aggiornata ogni minuto con le velocità TomTom
- **Updater daemon:** thread background che interroga TomTom e aggiorna la cache
- **TrafficWeighting:** implementazione di `Weighting` che usa la cache

**Flusso:**
```
[Import] GH edge → TomTom Flow Segment Data → edge→segmentId map (persistita)
[Runtime] TomTom Flow API → TrafficCache (in-memory) → TrafficWeighting → routing
```

**Vantaggi:**
- Alta precisione (a livello di singolo arco stradale)
- Dati real-time aggiornati ogni minuto come TomTom
- Possibilità di gestire chiusure stradali (`roadClosure=true`)

**Limitazioni:**
- Richiede sviluppo Java significativo (~2-4 settimane)
- Funziona **solo in Flexible Mode** (no CH): query più lente (~100-500ms)
- Il mapping GH edge↔TomTom segment è complesso (strutture dati diverse)
- Costo API TomTom per N edge × frequenza aggiornamento
- Il grafo è ricostruito da OSM: il mapping va rifatto ad ogni re-import

**Complessità di sviluppo:** ★★★★☆ (alta)

---

### Approccio C — Pre-processing: aggiornamento velocità al re-import

**Come funziona:**
1. Prima di importare il grafo, scaricare le velocità attuali da TomTom per tutti i segmenti stradali dell'area
2. Usare le velocità TomTom come override della velocità OSM durante l'import tramite `TagParser` personalizzato
3. Ricostruire il grafo (con CH se necessario) con le velocità aggiornate
4. Schedulare re-import periodici (es. ogni ora)

**Vantaggi:**
- Compatibile con Speed Mode (CH) → query molto veloci
- Snapshot accurato del traffico al momento dell'import
- Architettura semplice: GraphHopper viene usato normalmente

**Limitazioni:**
- Il grafo non è real-time: riflette lo stato del traffico al momento dell'ultimo import
- Il re-import di un grafo grande (es. Italia) richiede ore
- Non adatto a variazioni rapide del traffico
- Richiede una pipeline di build separata

**Complessità di sviluppo:** ★★★☆☆ (media)

---

### Approccio D — Routing ibrido: GraphHopper + TomTom Routing API

**Come funziona:**
1. Usare GraphHopper per il routing "base" (rotte statiche, evitare zone, ecc.)
2. Per richieste che richiedono traffico real-time, delegare a TomTom Routing API
3. Confrontare le due risposte e presentare la migliore

In alternativa, usare GraphHopper per calcolare il percorso e poi inviare il percorso calcolato alla TomTom Routing API per la "route reconstruction" con `traffic=true` e ottenere i tempi di percorrenza aggiornati.

**Vantaggi:**
- Sfrutta la qualità del traffico TomTom senza modificare GraphHopper
- Implementabile rapidamente (integrazione API-to-API)
- TomTom gestisce tutta la logica di traffico real-time e previsionale

**Limitazioni:**
- Doppio costo: GraphHopper infrastruttura + TomTom API calls
- Il routing di GraphHopper non "devia" automaticamente per il traffico
- Latenza doppia (due chiamate API)
- Limiti di utilizzo TomTom API

**Complessità di sviluppo:** ★★☆☆☆ (bassa)

---

### Approccio E — Custom Areas da Traffic Incidents (Incidenti, Bassa Latenza)

**Come funziona:**
1. Interrogare TomTom Traffic Incidents API per l'area di interesse
2. Per ogni incidente di tipo `ROAD_CLOSURE` o `JAM` significativo, creare un'area di penalità
3. Passare le aree come `areas` nel custom_model con `priority: multiply_by: 0` per le chiusure o `speed: multiply_by: 0.1-0.5` per i rallentamenti

Questo approccio è complementare all'Approccio A e si concentra sugli eventi puntuali invece che sul traffico diffuso.

**Complessità di sviluppo:** ★★☆☆☆ (bassa)

---

## 5. Traffico previsionale: TomTom e GraphHopper

### Cosa offre TomTom per il traffico previsionale

| Prodotto TomTom | Disponibilità | Note |
|---|---|---|
| `historicTrafficTravelTimeInSeconds` nel Routing API | API pubblica | Solo come output di routing, non come dato raw |
| `departAt` nel Routing API | API pubblica | Routing time-dependent con traffico storico |
| **Historical Traffic Analytics** | Enterprise/contratto separato | Dati storici di velocità per segmento per ora del giorno |

TomTom **non espone un'API pubblica standalone** per ottenere "la velocità prevista alle 8:00 di martedì sulla A4 tra Milano e Brescia". Questi dati sono disponibili solo tramite il prodotto enterprise o implicitamente tramite il loro Routing API.

### Come implementare il traffico previsionale in GraphHopper

Per implementare il routing time-dependent in GraphHopper (es. "partendo alle 8:15 di lunedì, quali sono le velocità previste?") esistono due strade:

**Opzione 1 — Profili di velocità per ora/giorno (con dati storici TomTom Enterprise)**

Se si ottiene accesso ai dati Historical Traffic Analytics di TomTom:
1. Costruire per ogni edge GH una tabella `(ora, giorno_settimana) → velocità`
2. Implementare un `TimeDependentWeighting` Java che usa la tabella
3. La richiesta di routing include l'orario di partenza (`departure_time`)
4. Il weighting usa la velocità appropriata per l'ora di ogni arco

Questa è l'architettura più completa ma richiede:
- Accesso a dati TomTom Enterprise (costo da valutare)
- Sviluppo Java significativo (~4-8 settimane)
- Storage aggiuntivo per le tabelle di velocità (ordine di grandezza: GigaByte per una regione)
- Incompatibile con CH (necessaria Flexible Mode o LM)

**Opzione 2 — Proxy verso TomTom Routing API per i tempi di percorrenza**

Per casi d'uso dove si vuole solo confrontare "quanto ci vuole ora" vs "quanto ci vorrebbe alle 8:00 di domani":
1. GraphHopper calcola il percorso geometrico
2. Si invia il percorso a TomTom con `departAt` e `computeTravelTimeFor=all`
3. TomTom restituisce il tempo stimato con traffico storico/previsionale
4. Si usa il dato TomTom per ETA e avvisi di traffico

---

## 6. Matrice di confronto degli approcci

| Approccio | Precisione | Real-time | Previsionale | Sviluppo | Performance GH | Costo TomTom |
|---|---|---|---|---|---|---|
| **A** — Custom Areas Flow | Bassa (zona) | ✅ Sì | ❌ No | Bassa | Media (LM) | Basso |
| **B** — Custom Weighting Java | Alta (arco) | ✅ Sì | ❌ No* | Alta | Bassa (Flex) | Alto |
| **C** — Pre-processing import | Alta (arco) | ❌ No (snapshot) | ❌ No | Media | Alta (CH) | Medio |
| **D** — Routing ibrido GH+TomTom | Alta | ✅ Sì | ✅ Sì (via TomTom) | Bassa | Alta (GH puro) | Alto |
| **E** — Areas da Incidents | Media (zona) | ✅ Sì | ❌ No | Bassa | Media (LM) | Basso |

> \* Con dati Historical Traffic Analytics TomTom Enterprise l'Approccio B può essere esteso al traffico previsionale.

---

## 7. Raccomandazioni e next steps

### Scenario 1: Integrazione rapida, bassa precisione

**Approccio consigliato: A + E combinati**

- Sviluppo stimato: 2-4 settimane
- Funziona senza modifiche al core GraphHopper
- Implementare come middleware Java/Python che pre-processa le richieste di routing aggiungendo le `areas` dal traffico TomTom
- Limitazione: precisione geografica bassa (zone, non archi stradali)

### Scenario 2: Integrazione production, alta precisione real-time

**Approccio consigliato: B (Custom Weighting)**

- Sviluppo stimato: 6-10 settimane
- Richiede fork/extension del codebase GraphHopper
- Sviluppare: (a) mapper GH↔TomTom durante import, (b) cache in-memory con aggiornamenti periodici, (c) `TrafficWeighting` Java
- La Flexible Mode impatta le prestazioni: valutare se accettabile per il caso d'uso
- Considerare l'uso di una cache distribuita (Redis) per la Traffic Cache in ambienti multi-istanza

### Scenario 3: Massima qualità, traffico previsionale

**Approccio consigliato: D (Routing ibrido)**

- Sviluppo stimato: 1-3 settimane
- Usare GraphHopper per routing statico (isocrone, ottimizzazioni geometriche) e TomTom per i tempi di percorrenza con traffico
- Verificare con TomTom la disponibilità e il costo del prodotto **Historical Traffic Analytics** per il traffico previsionale puro in GH

### Domande da chiarire con TomTom

1. Il contratto aziendale include accesso a **Historical Traffic Analytics** (dati storici per segmento)?
2. Quali sono i **rate limits** per il Traffic Flow Segment Data API?
3. È disponibile un'API per **bulk download** delle velocità per una bounding box (evitare N chiamate per N edge)?
4. Qual è la **copertura geografica** per l'area di interesse (Italia nord-ovest)?

---

## Appendice: Risorse tecniche

| Risorsa | Link |
|---|---|
| GraphHopper Custom Models docs | [docs/core/custom-models.md](./core/custom-models.md) |
| GraphHopper Custom Areas docs | [docs/core/custom-areas-and-country-rules.md](./core/custom-areas-and-country-rules.md) |
| GraphHopper Profiles docs | [docs/core/profiles.md](./core/profiles.md) |
| GraphHopper Weighting docs | [docs/core/weighting.md](./core/weighting.md) |
| TomTom Traffic API | https://developer.tomtom.com/traffic-api/documentation/product-information/introduction |
| TomTom Flow Segment Data API | https://developer.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data |
| TomTom Routing API (Calculate Route) | https://developer.tomtom.com/routing-api/documentation/routing/calculate-route |
| TomTom Traffic Incidents API | https://developer.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-incidents/traffic-incidents-service |
