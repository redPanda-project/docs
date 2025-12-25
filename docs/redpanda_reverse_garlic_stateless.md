# Redpanda – Reverse Garlic (stateless) (Draft)

> Ziel: **Keine direkten DHT-Reads durch Clients**, weil DHT-Queries Metadaten leaken könnten („wer will was von wem?“).  
> Lösung: **Reverse Garlic** als *stateless Reply Block* (SURB-artig), der Antworten **rückwärts** zu Alice zustellt, ohne dass Zwischenknoten etwas ablegen müssen.

---

## 1) Problem

Direktes Lesen aus der DHT kann Metadaten offenbaren:
- Beobachter sehen *welcher* Client *welchen* Key/Record anfragt.
- Daraus können Kommunikationsbeziehungen oder Interessen abgeleitet werden.

Wir wollen stattdessen:
- Alice sendet eine Query *ins Netz* (über Routing/Flaschenpost/Garlic),
- die Query enthält einen **Reverse-Garlic-Reply-Block**,
- jeder Responder kann die Antwort **über diesen Block** zu Alice zurücksenden,
- ohne Alice’ Identität/Erreichbarkeit preiszugeben,
- und ohne dass Zwischenhops state halten müssen.

---

## 2) Begriff: Reverse Garlic (stateless)

**Reverse Garlic (stateless)** ist ein *Reply-Block* (ähnlich SURB), der:
- aus **mehreren verschlüsselten Hop-Layern** besteht,
- **von Alice** erstellt wird („von innen nach außen“ / rückwärts befüllt),
- von einem Responder benutzt werden kann, um ein Payload anonym zu Alice zu schicken,
- wobei jeder Hop nur „Layer entschlüsseln → Next-Hop“ macht.

Eigenschaften:
- **stateless Hops**: keine pro-Message Speicherung (außer optional Replay-Filter mit kurzem TTL).
- **single-use** (empfohlen): jeder Block ist nur einmal gültig (verhindert Linkability/Replay).
- **expiry**: Block läuft ab (DoS-/Replay-Reduktion).

---

## 3) Zusammenhang zu Outbound Handle (OH)

Reverse Garlic liefert am Ende zu einem **Outbound Handle** von Alice (OH).  
Der Outbound Server öffnet die **letzte Garlic-Schicht** und findet darin z.B.:
- `push` (die Antwort / Daten)
- `ack` (optional, Quittungsinformationen)

> Wichtig: Reverse Garlic ist der „Rückweg“, OH ist der **letzte Zustell-Endpunkt**.

---

## 4) High-Level Ablauf

### 4.1 Build (Alice)
1. Alice wählt einen Rückweg über `n` Hops: `H1 -> H2 -> ... -> Hn`.
2. Alice erstellt den Reply Block „von innen nach außen“:
   - Innerstes Layer enthält Final Delivery (Alice’ OH) und Payload-Key.
   - Dann wird mit Hop-Key von `Hn` verschlüsselt + MAC.
   - Ergebnis wird mit Hop-Key von `H(n-1)` verschlüsselt + MAC.
   - ... bis `H1`.

Ergebnis: `ReverseGarlicBlock` (RGB)

### 4.2 Use (Responder)
1. Responder nimmt `RGB` aus Alice’ Query.
2. Responder verschlüsselt die Antwort mit `payload_key`:
   - `payload = Enc(payload_key, response_data)`
3. Responder sendet `(RGB, payload)` an `H1`.

### 4.3 Forward (Hops)
Jeder Hop macht deterministisch:
1. Layer-Key finden (über Tag/Key-Index)
2. MAC prüfen
3. Layer decrypt → `next_hop` + `remaining_block`
4. weiterleiten an `next_hop`

### 4.4 Deliver (Outbound Handle)
Letzter Hop liefert an Alice’ `OH`.
Outbound Server öffnet finale Schicht und stellt `payload` Alice zu.

---

## 5) Datenmodell (Draft)

### 5.1 ReverseGarlicBlock (RGB)
Enthält:
- `version`
- `expiry_ts`
- `nonce` / `block_id` (für Replay-Schutz / Single-Use)
- `entry_hop` (Adresse/ID von `H1`)
- `hop_layers[]` (opaque bytes; bereits „onion“-verschachtelt)
- `payload_params` (z.B. payload size cap, cipher suite id)

> `entry_hop` kann auch indirekt (Handle/ID) sein, wenn H1 via DHT/Directory gefunden wird.

### 5.2 Hop Layer (pro Hop, verschlüsselt)
Nach dem Decrypt eines Layers erhält der Hop:
- `next_hop` (Adresse/ID; beim letzten Layer: Final Delivery)
- `key_id` / `tag` (für nächsten Hop)
- `mac_next` / `integrity` (optional, je Konstruktion)
- `rest` (remaining onion bytes)

### 5.3 Final Delivery (im innersten Layer)
- `OHDescriptor` (Alice’ Outbound Handle):
  - `server_endpoint`
  - `handle_id`
  - optional `OH_auth_pub` (wenn für den letzten Schritt nötig)
- `payload_key` (symmetrisch, nur für diese Antwort)
- optional `ack_key` (falls ACK/Receipt im Rückweg enthalten ist)

---

## 6) Schlüsselmechanik (stateless, empfohlen)

### 6.1 Session Tags / Key Lookup (schnell)
Damit Hops ohne teure Public-Key-Operation pro Message arbeiten können:
- Alice kann **vorab** „Session Tags“ / Key-Handles an Hops senden (über normale Garlic).
- Der Reverse Block referenziert diese Tags (`tag`), sodass der Hop den passenden symm. Key findet.

### 6.2 Alternative: DH pro Block (teurer, aber self-contained)
- Jeder Hop-Key wird via DH aus einem Hop-Public-Key abgeleitet.
- Vorteil: komplett selbstbeschreibend.
- Nachteil: mehr CPU/Bytes (aber noch machbar, je nach Frequenz).

> Für mobile Effizienz: **SessionTag-Variante** bevorzugen.

---

## 7) Sicherheits-/Privacy-Eigenschaften

- **Keine direkten DHT-Reads** durch Alice für die eigentliche Datenbeschaffung.
- **Responder kennt Alice nicht** (nur den Reply-Block und Entry-Hop).
- **Hops sehen nur Next-Hop**, nicht Quelle/Ziel (klassische Onion-Eigenschaft).
- **Single-Use + expiry** reduziert Replay und Linkability.
- **Optionaler Replay-Filter** an Hops (kurzer TTL Bloomfilter) verbessert Robustheit, bleibt „quasi-stateless“.

---

## 8) Design-Entscheidungen (noch offen)

1. **Single-Use Enforcement**
   - minimal: `block_id` + expiry
   - robust: `block_id` in Hop-Replayfilter

2. **Payload Size**
   - max size pro RGB (DoS)
   - chunking/protocol für große History

3. **Fehlerverhalten**
   - wenn Hop nicht erreichbar: fallback (mehrere RGBs) oder Retry mit neuem Pfad

4. **Woher kennt Alice die Hop-Public-Keys / SessionTags?**
   - Directory/Bootstrap (offline)
   - oder über bereits bestehende Channel-Kommunikation

---

## 9) Kurzbeispiel (Intuition)

- Alice sendet: „Wer hat Record X?“ + RGB (Reply-Block)
- Bob (oder ein Holder) antwortet: nutzt RGB, packt Antwort in payload_key-Encrypt, sendet an H1
- Nachricht wandert H1→H2→H3→…→OH
- Alice bekommt Antwort, ohne jemals DHT-get(X) direkt zu machen.

---

## 10) Glossar

- **DHT**: Distributed Hash Table (Discovery/Storage)
- **Garlic Message**: mehrschichtige Nachricht (bundle/route)
- **Reverse Garlic / Reply Block**: vordefinierter Rückweg (SURB-artig)
- **OH (Outbound Handle)**: servergebundener Zustell-Endpunkt
- **Outbound Server**: hostet OHs, öffnet letzte Schicht, extrahiert push/ack
- **Flaschenpost**: Projektbegriff, nicht übersetzen
