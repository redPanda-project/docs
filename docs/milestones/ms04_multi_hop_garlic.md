# MS04: Multi-Hop Garlic Routing

## Status: Done — Backend (2026-06-12, redpandaj [#224](https://github.com/redPanda-project/redpandaj/pull/224)), Frontend (2026-06-12, mobile [#29](https://github.com/redPanda-project/redpanda-mobile/pull/29))

Serverseitig ist das Flaschenpost-v2-Relay komplett: Layer-Peeling, Rebuild + Re-Padding,
Kademlia-Forwarding, Dedup und der Peer-Key-Austausch stehen (verbindliche Wire-Formate: siehe
[Decisions (Backend-MS04)](#decisions-backend-ms04-2026-06-12)). Der Mobile-Client baut
3-Layer-Garlic-Pakete und routet `sendMessage()` über `FLASCHENPOST_V2` (clientseitige
Festlegungen: [Decisions (Frontend-MS04)](#decisions-frontend-ms04-2026-06-12)).

## Goal

Route messages through 3 intermediate full nodes (hops), where each hop can only decrypt its own layer to learn the next hop. No single node knows both sender and recipient. Messages are padded to a fixed size to prevent traffic analysis.

## Prerequisites

- MS03 (Authenticated Encryption) — Ed25519/X25519 + AES-256-GCM in place

## Current State

| What | Where | Status |
|------|-------|--------|
| Single-layer garlic encryption | `GarlicMessage.java` | Done — v2: AES-256-GCM + X25519 + HKDF (MS03) |
| `Flaschenpost` base class | `Flaschenpost.java` | Done — destination KademliaId, dedup ID, timestamp |
| GMType enum | `GarlicMessage.java` | Done — GARLIC_MESSAGE(2) = Versions-Byte (MS03) |
| GM dedup cache | `GMStoreManager.java` | Done — 5-minute window, v1 messages + v2 packet_ids (MS04) |
| Kademlia routing | `KadStoreManager.java`, peer routing tables | Done — can find nodes by KademliaId |
| **Flaschenpost v2 packets** | `FlaschenpostV2.java` | Done (MS04) — fixe 2048 B, Layer-Krypto, Build/Parse |
| **Relay peeling/forwarding** | `GarlicRouter.java`, `Command.FLASCHENPOST_V2` (142) | Done (MS04) — CMD_FORWARD/CMD_DELIVER, Kademlia-Step |
| **Peer encryption keys** | `PeerInfoProto.encryption_public_key` | Done (MS04) — 32-byte X25519 im Peer-Austausch |
| **Mobile garlic builder + hop selection** | `garlic/garlic_builder.dart`, `garlic/hop_selector.dart` | Done (Frontend MS04) — 3-Layer-Pakete, Hop-Auswahl, `sendMessage()` via Command 142; ersetzt `garlic_message_wrapper.dart` |

## Spec

### 1. Packet Format (Flaschenpost v2)

Each garlic packet has a fixed size of **2048 bytes** (configurable constant):

```
Flaschenpost v2 (verbindlich, siehe Decision 1):
  [1  version = 0x02]
  [4  packet_id]          // random, for dedup
  [20 next_hop]           // KademliaId of next relay (or final destination)
  [12 nonce]              // AES-256-GCM nonce
  [32 ephemeral_pub]      // X25519 ephemeral public key for this layer
  [4  ciphertext_len]     // Länge von ciphertext inkl. 16-byte GCM-Tag
  [N  ciphertext + tag]   // AES-256-GCM ciphertext, Tag in N enthalten
  [P  random padding]     // Auffüllung auf 2048 bytes total
  ─────────────────────
  Total = 2048 bytes fix (Header 73 B, max. Plaintext 2048 − 73 − 16 = 1959 bytes)
```

### 2. Layer Construction (Sender)

Alice builds a 3-hop garlic message to reach Bob's OH node:

```
Path: Alice → H1 → H2 → H3 → OH_node

Layer 3 (innermost, for H3):
  plaintext_3 = [1 CMD_DELIVER][20 OH_kademlia_id][remaining: actual_message + padding]
  layer_3 = encrypt(H3.enc_pub, plaintext_3)

Layer 2 (for H2):
  plaintext_2 = [1 CMD_FORWARD][20 H3_kademlia_id][remaining: layer_3 + padding]
  layer_2 = encrypt(H2.enc_pub, plaintext_2)

Layer 1 (outermost, for H1):
  plaintext_1 = [1 CMD_FORWARD][20 H2_kademlia_id][remaining: layer_2 + padding]
  layer_1 = encrypt(H1.enc_pub, plaintext_1)
```

Each layer's plaintext starts with a command byte:
- `CMD_FORWARD (0x01)`: Peel this layer, forward the inner packet to `next_hop`.
- `CMD_DELIVER (0x02)`: This is the final hop; deliver the payload to the local OH service.

### 3. Layer Peeling (Relay)

When a full node receives a Flaschenpost v2:

1. Check `packet_id` in dedup cache; drop if seen.
2. Try to decrypt `encrypted_payload` using own X25519 private key + `ephemeral_pub`.
3. If decryption fails → this packet is not for us; ignore (do NOT flood-forward).
4. If decryption succeeds:
   - Read command byte.
   - `CMD_FORWARD`: Extract `next_hop` KademliaId and inner packet. Re-pad to 2048 bytes. Forward to `next_hop` via Kademlia routing.
   - `CMD_DELIVER`: Extract payload. Pass to `OutboundService` for mailbox deposit.

### 4. Hop Selection

**Sender (mobile client) picks 3 hops:**

1. Query the local peer table for known full nodes.
2. Select 3 nodes that are:
   - Online (recently seen).
   - Not the sender's own connected node (avoid trivial correlation).
   - Not the destination OH node.
   - Geographically / topologically diverse (if metadata available).
3. For each hop, the sender needs the node's X25519 encryption public key (available via the Kademlia DHT or peer exchange).

**Hop key discovery:**
- During peer list exchange (`SendPeerList`), include the encryption public key in `PeerInfoProto`.
- Alternatively, query the DHT for a node's current public key.

### 5. Padding

- Every Flaschenpost v2 packet is exactly 2048 bytes.
- After constructing the inner plaintext, pad with random bytes to fill the remaining space.
- This prevents an observer from determining the message length or the number of remaining hops.

### 6. Dummy Traffic (Optional, Deferred)

For stronger anonymity, nodes can periodically send dummy Flaschenpost packets (encrypted random bytes to random KademliaIds). This is deferred to a later milestone but the packet format should accommodate it.

## Protobuf Changes

**`commands.proto`:**
```protobuf
// Add encryption public key to peer info
message PeerInfoProto {
  string ip = 1;
  int32 port = 2;
  NodeIdProto node_id = 3;
  bytes encryption_public_key = 4;  // NEW: 32-byte X25519 public key
}
```

No other proto changes — the Flaschenpost v2 format is binary, not protobuf.

## Backend Changes

| File | Action |
|------|--------|
| **New**: `FlaschenpostV2.java` | Parse/serialize v2 packets, fixed 2048-byte size |
| **New**: `GarlicRouter.java` | Layer peeling logic: decrypt → CMD_FORWARD/CMD_DELIVER dispatch |
| `GarlicMessage.java` | Bleibt in Nutzung (Single-Layer v2 für interne Pfade, kein Deprecate); v1 wird seit MS03 nicht mehr geparst — siehe Decision 11 |
| `InboundCommandProcessor.java` | Add handler for `FLASCHENPOST_V2` command type |
| `ConnectionHandler.java` | Forward v2 packets to Kademlia-routed next hop |
| `GMStoreManager.java` | Extend dedup cache to v2 packet IDs |
| `PeerList` handling | Include X25519 encryption public key in peer exchange |

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `garlic_builder.dart` | Build 3-layer garlic packets (layer construction) |
| **New**: `hop_selector.dart` | Select 3 relay nodes from known peers |
| `redpanda_light_client.dart` | Use `garlic_builder` in `sendMessage()` instead of single-layer |
| `garlic_message_wrapper.dart` | Update for v2 format |
| `peer_repository.dart` | Store encryption public keys for known peers |

## Acceptance Criteria

- [x] A message traverses exactly 3 intermediate hops before reaching the OH node *(Backend: `GarlicRouterTest` 3-Relay-E2E; Frontend baut die Pfade in Frontend-MS04)*
- [x] Each relay can only see the next hop, not the final destination or the sender *(Layer-Krypto, AAD = next_hop)*
- [x] All Flaschenpost v2 packets are exactly 2048 bytes regardless of payload size *(Backend erzwingt und parst fixe 2048 B und füllt beim Rebuild neu auf)*
- [x] A relay that is not on the path cannot decrypt the packet (decryption fails gracefully)
- [x] Dedup prevents the same packet from being forwarded twice by any node
- [x] Hop selection avoids the sender's direct node and the destination OH node *(`HopSelector`: Ausschluss nach Adresse und KademliaId; OH-Endpoint via `addChannelKeys(peerOhEndpoint:)`)*
- [x] Messages still arrive reliably (MS02 retry logic works with multi-hop) *(Frontend-E2E `ms04_multi_hop_garlic_test.dart`: Re-Send mit stabiler message_id über frische Hops, Dedup empfängerseitig)*

## Decisions (Backend-MS04, 2026-06-12)

Umgesetzt in redpandaj [#224](https://github.com/redPanda-project/redpandaj/pull/224). Wire-Format wie in der [Backend-View](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms04_multi_hop_garlic.md) (Variante **mit explizitem `ciphertext_len`** — die „Overhead 85, kein Längenfeld"-Rechnung in Sektion 1 oben ist obsolet). Folgende Festlegungen sind **für Frontend MS04 verbindlich**:

1. **Paketformat (fix 2048 B)**: `[1 version=0x02][4 packet_id][20 next_hop][12 nonce][32 ephemeral_pub][4 ciphertext_len][N ciphertext inkl. Tag][P random padding]`. Header = 73 B. **`ciphertext_len` zählt den Ciphertext einschließlich des 16-byte GCM-Tags**, gültig ∈ [17, 1975]. Padding ist zufällig und wird ignoriert (`ciphertext_len` ist maßgeblich).
2. **Transport**: neues Top-Level-Command **`FLASCHENPOST_V2 = 142`**, geframed wie alle Payload-Commands (`[cmd][len:4][2048-B-Paket]`) — *nicht* in `FlaschenpostPut` eingebettet. Light Clients senden ihr fertiges Garlic-Paket über ihren verbundenen Full Node; Full Nodes leiten v2-Pakete nur an Full-Node-Peers weiter (Light Clients sind nie Relays).
3. **Layer-Krypto**: `key = HKDF-SHA256(ikm = X25519(ephemeralPriv, hop.encryptionPub), salt = ephemeral_pub, info = "flaschenpost-v2", 32)`, AES-256-GCM, **AAD = 20-byte `next_hop`-KademliaId der Schicht** — ein auf einen anderen Relay umgebogenes Paket schlägt bei der Authentifizierung fehl. Domain-getrennt vom Single-Layer-Garlic (`"garlic-v2"`).
4. **Layer-Plaintexte**: `CMD_FORWARD (0x01)` = `[1 cmd][20 inner_next_hop][12 nonce][32 ephemeral_pub][4 ct_len][ct+tag]` (exakt der „Body" der nächsten Schicht). `CMD_DELIVER (0x02)` = `[1 cmd][20 oh_id][4 payload_len][payload][optionales Padding]`. **`oh_id` ist 20 Bytes** (KademliaId — die 32 im Pseudo-Code der Backend-View waren ein Fehler); das explizite `payload_len` erlaubt dem Sender, den innersten Plaintext beliebig zu padden.
5. **Rebuild beim Peelen**: neue zufällige `packet_id` (Dedup-Caches der Folge-Hops kollidieren nie) + neues Random-Padding auf 2048 B; der Body wird opak übernommen. Pro FORWARD-Schicht schrumpft `ciphertext_len` um 85 B (21 B Layer-Header + 48 B Body-Header + 16 B Tag) — das sichtbare `ciphertext_len` leakt damit grob die Restpfadlänge; akzeptiert (KISS), Mitigation wäre Dummy-Traffic (deferred, OQ 5).
6. **Größenbudget**: max. Plaintext der äußersten Schicht 1959 B; bei 3 Hops (2× FORWARD + 1× DELIVER) max. **1764 B Deliver-Payload**. Mit Channel-Envelope v4 (69 B + ~27 B inneres ChannelMessage, MS03b) bleiben ~1,65 KiB Content — keine Fragmentierung in MS04 (OQ 1 Master / OQ 2 Backend-View → deferred).
7. **Loop-/Replay-Schutz**: Dedup auf `packet_id` (`GMStoreManager`, 5-Minuten-Fenster) stoppt Replays und Routing-Loops des **unveränderten** Pakets (Kademlia-Steps behalten die packet_id). Gepeelte FORWARD-Ketten erhalten zwar eine frische packet_id, sind aber durch die Schichtenzahl physikalisch begrenzt (jede Schicht kostet 85 B des 2048-B-Pakets) — unbegrenzte Relay-Loops kann ein Sender nicht konstruieren. Bewusst **kein hop_count im Paket** — er würde die Position im Pfad leaken; Relays bleiben stateless.
8. **Routing & Fehlerverhalten**: Das Routing entscheidet **vor jedem Decrypt** allein anhand des `next_hop`-Headers — nur Pakete, deren `next_hop` auf die eigene Node zeigt, werden überhaupt entschlüsselt (ein Auth-Fehler bedeutet dann: nicht für uns verschlüsselt → Silent Drop). Pakete mit fremdem `next_hop` werden **unverändert** (gleiche packet_id) über die gemeinsame Next-Peer-Auswahl weitergeleitet (direkt verbunden → gewichteter Node-Graph → greedy Kademlia nur bei striktem Fortschritt; `OhForwarder.selectNextPeer`). Kein Retry, Silent Drop ohne Route — Zustellsicherheit liefert die MS02-Retry-Logik des Senders (Backend-View OQ 3).
9. **`CMD_DELIVER`**: `outboundService.depositMessage(oh_id, payload)`; bei `NOT_FOUND` Fallback auf das MS02b-`OhForwarder`-Forwarding (hop_count 0) — der letzte Garlic-Hop muss nicht der OH-Host sein. **Keine Bestätigung** an den Sender (Backend-View OQ 1 → R-ACK erst MS06).
10. **`PeerInfoProto.encryption_public_key` (Feld 4)**: 32-byte X25519-Key, gesetzt sobald die NodeId des Peers bekannt ist. Redundant zu Bytes 32..63 des 64-byte `node_id`-Exports, aber explizit für Light Clients (Hop-Auswahl ohne NodeId-Import); empfängerseitig bleibt `node_id` maßgeblich.
11. **`GarlicMessage` (Single-Layer v2) wird nicht deprecated** — es wird weiter intern genutzt (Node-zu-Node, Perf-Tests); das „v1 backward compat"-Item der Spec ist seit MS03 obsolet (v1 wird nicht mehr geparst). Die Client-Sendpfade wechseln mit Frontend MS04 auf Flaschenpost v2.
12. **Hop-Anzahl**: Das Backend erzwingt keine Schichtenzahl — sie bestimmt der Sender (Frontend wählt 3; konfigurierbar clientseitig, OQ 2).

## Decisions (Frontend-MS04, 2026-06-12)

Umgesetzt in redpanda-mobile [#29](https://github.com/redPanda-project/redpanda-mobile/pull/29), aufbauend auf den [Decisions (Backend-MS04)](#decisions-backend-ms04-2026-06-12):

1. **Hop-Anzahl & Degradierung**: Standard sind **3 Hops** (`defaultHopCount`, clientseitige Konstante). Sind weniger eligible Kandidaten bekannt, sendet der Client mit weniger Hops und loggt eine Warnung (reduzierte Privatsphäre); **ohne** Kandidaten fällt `sendMessage()` auf den direkten MS02b-Deposit (`FlaschenpostPut` + `want_response`) zurück — das „Minimum 1 Hop"-Item der Frontend-Spec ist damit der MS01-äquivalente Direktpfad. Vor dem Senden fordert der Client per `requestPeerLists()` frische Peer-Listen an, wenn Kandidaten fehlen (Spec Sektion 5, Punkt 1 — im E2E-Test als Poll-Loop genutzt).
2. **Anonymes Fetching**: `fetchMessages()` bleibt direkt — ein anonymer Fetch braucht einen Rückkanal und ist damit **MS05 (Reverse Garlic)**. Das Garlic-Routing gilt in MS04 nur für den Sendepfad.
3. **Hop-Failure**: Komplettes Re-Send mit **frisch gewählten Hops** über die bestehende MS02-Retry-Queue; kein Ersetzen einzelner Hops (ohne R-ACK — MS06 — ist ein Hop-Ausfall clientseitig nicht beobachtbar). Re-Sends tragen dieselbe innere `message_id` und werden empfängerseitig dedupliziert; auf dem Garlic-Pfad gibt es **keine** Deposit-Bestätigung, die Nachricht gilt mit der Submission als übergeben (Master-Spec OQ 4 → beantwortet).
4. **Paketgröße**: Fix **2048 B** (`GarlicBuilder.packetSize`, Konstante wie im Backend); nicht konfigurierbar. Payload-Budget-Guard: Inhalte über `maxPayloadLength(hops)` (1764 B bei 3 Hops) schlagen permanent als `BAD_REQUEST` fehl (keine Fragmentierung, Backend-Decision 6).
5. **Hop-Diversität**: Kein Mindest-Peer-Schwellwert (KISS). Auswahl = Secure-Shuffle + Greedy-Präferenz für unterschiedliche KademliaId-Präfixe (erstes Byte); bei zu uniformer Kandidatenmenge wird mit den restlichen Kandidaten aufgefüllt. Derselbe Node unter mehreren Adressen wird höchstens einmal pro Pfad verwendet.
6. **Ausschlüsse**: Submit-Node (nach Adresse **und** entdeckter KademliaId — Seed-Aliase!) und der Ziel-OH-Endpoint werden nie Hops. Dafür speichert `addChannelKeys()` neu den `peerOhEndpoint` des Kanals.
7. **Peer-Key-Quellen & Persistenz**: `PeerInfoProto.encryption_public_key` (Feld 4) wird geparst; Fallback sind Bytes 32..63 des 64-byte `node_id`-Exports (Backend-Decision 10). Der Key des direkt verbundenen Nodes wird beim Handshake aus dessen Public-Key-Export gelernt. Persistenz in Drift **Schema v11** (`Peers.encryption_public_key`, non-destruktiv; Spec nannte v8 — Stand war inzwischen v10). Hinweis: der Netzwerk-Isolate nutzt weiterhin `InMemoryPeerRepository` (C4-TODO der Frontend-Übersicht), lernt Keys also pro Session aus dem Peer-Austausch; die Drift-Spalte greift mit der C4-Verkabelung.
8. **`garlic_message_wrapper.dart` entfernt** — der Single-Layer-Wrapper war nie in den Netzwerkpfad integriert und ist durch `garlic_builder.dart` ersetzt (Backend-Decision 11: serverseitiges `GarlicMessage` bleibt unberührt).

## Open Questions

Backend-seitig beantwortet durch die [Decisions (Backend-MS04)](#decisions-backend-ms04-2026-06-12):

1. ~~Fixed packet size: 2048 bytes enough?~~ → Ja, 1764 B Payload-Budget bei 3 Hops; Fragmentierung deferred (Decision 6).
2. ~~How many hops? Should it be configurable?~~ → Sender-Entscheidung, Backend agnostisch; Frontend startet mit 3 (Decision 12).
3. ~~Return path (RGB) in den Garlic-Layers?~~ → Strikt MS05; das Layer-Format ist über das Command-Byte erweiterbar.
4. ~~How to handle the case where a selected hop node is offline? Retry with different hops, or fall back to fewer hops?~~ → Komplettes Re-Send mit frischen Hops via MS02-Retry-Queue (Frontend-Decision 3; serverseitig best-effort/silent drop, Decision 8).
5. ~~Should dummy traffic be part of this milestone or deferred?~~ → Deferred; das Format trägt Dummy-Pakete ohne Änderung (Decision 5).
