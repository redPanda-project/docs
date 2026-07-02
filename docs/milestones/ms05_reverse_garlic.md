# MS05: Reverse Garlic

## Status: Done — Backend (2026-06-13, redpandaj [#226](https://github.com/redPanda-project/redpandaj/pull/226)), Frontend (2026-07-02, mobile [#33](https://github.com/redPanda-project/redpanda-mobile/pull/33))

ARC42 (`04_solution_strategy.adoc`, `06_runtime_view.adoc`, `08_concepts.adoc`) specifies Reverse Garlic Blocks (RGBs) as the mechanism for reply paths.

Serverseitig ist der Reverse-Pfad komplett: Relays peelen Reverse-Pakete mit unveränderter
MS04-Logik, der finale Hop legt getaggte Delivers (`CMD_DELIVER_TAGGED`) mit 16-Byte
`session_tag` in der OH-Mailbox ab, `FetchResponse` liefert den Tag an den Client
(verbindliche Festlegungen inkl. RGB-Inhaltsmodell für das Frontend: siehe
[Decisions (Backend-MS05)](#decisions-backend-ms05-2026-06-13)). Clientseitig hängt der
Mobile-Client eine frische RGB an jede ausgehende Nachricht, baut Replies als getaggte
MS04-Onions über die RGB-Hops und korreliert eingehende Replies single-use über den
Session-Tag ([Decisions (Frontend-MS05)](#decisions-frontend-ms05-2026-07-02)).

## Spike Required Before MS04 Implementation

> **This spike must be completed before MS04 is implemented, not after.** Its outcome decides whether the RGB concept fits in a packet at all, and freezing the RGB format prematurely risks re-learning attacks that the prior art already documents.

Two questions must be answered first:

1. **Size budget.** Compute whether a 3-hop RGB plus padding fits inside the Flaschenpost v2 budget. Per MS04, a 2048-byte Flaschenpost v2 packet leaves **1959 payload bytes** (2048 − 73 header − 16 GCM tag; die früher hier genannten 1963 stammten aus der obsoleten „kein Längenfeld"-Rechnung, vgl. MS04 Decision 1). Each onion layer adds a destination (20-byte KademliaId), an ephemeral public key, an AEAD nonce + tag, and padding; three nested layers plus the `ChannelMessage` content compete for those 1963 bytes. The spike must produce a concrete byte accounting and confirm a 3-hop RGB + a usable `content` fits — or determine the maximum hop count / content size that does, **before** the wire format and `ReverseGarlicBlock` layout are frozen.

2. **Prior-art review.** Reverse Garlic Blocks re-invent **I2P SURBs** (Single-Use Reply Blocks) and **Sphinx reply blocks**. These have documented attack classes that the current MS05 Open Questions re-ask from scratch (single-use vs. reusable, batch size, size vs. packet budget). The spike must review and explicitly address their known attack classes before the RGB format is frozen:
   - **Tagging attacks** on reply blocks (a malicious hop marks a packet to correlate it downstream).
   - **Replay** of a reply block (re-using a captured RGB to flood / correlate).
   - **Size correlation** (RGB or padding size leaks path length or links forward and reply paths).

   The MS05 Open Questions below (single-use? batch size? size vs. 2048-byte limit?) are largely answered in the SURB/Sphinx literature — answer them from that literature rather than re-deriving them and paying the tuition twice.

**Deliverable of the spike:** a short byte-budget table (per hop, per layer) confirming feasibility, and a one-page mapping of each Sphinx/SURB attack class to the chosen RGB defense (or an explicit decision to accept the risk). MS05 implementation does not start until both are signed off.

> **Erledigt (rückwirkend nachgeholt mit Backend-MS05, 2026-06-13):** Der Spike war als
> Vorbedingung für MS04 gedacht, wurde aber erst mit Backend-MS05 dokumentiert — er blockiert
> nichts mehr. Byte-Budget und Attack-Class-Mapping stehen in
> [Decisions (Backend-MS05)](#decisions-backend-ms05-2026-06-13), Decisions 7–8. Kernergebnis:
> ein 3-Hop-Reply mit getaggtem Deliver lässt 1748 B Payload (passt); das SURB-Kernproblem
> (vorverschlüsselte Reply-Blöcke brauchen per-Hop-Payload-Transformation à la Sphinx) wird
> umgangen, indem der Responder die Reply-Onion selbst baut (Decision 6).

## Goal

Enable Bob to send a reply to Alice without knowing Alice's network location or OH node. Alice pre-builds an encrypted reply path (RGB) and includes it in her outgoing message. Bob uses the RGB to route his reply back through Alice's chosen hops to Alice's OH.

## Prerequisites

- MS04 (Multi-Hop Garlic) — forward-path garlic routing working

## Current State

| What | Where | Status |
|------|-------|--------|
| RGB data model | ARC42 `08_concepts.adoc`; verbindlich: Decision 6 | Festgelegt (Hop-Deskriptoren statt encrypted_layers) |
| Reply block concept | ARC42 `06_runtime_view.adoc` | Runtime diagram only |
| Session tags (Server-Seite) | `CMD_DELIVER_TAGGED`, `MailItem.session_tag`, `FlaschenpostPut.session_tag` | Done (Backend-MS05) |
| Session tags (Client-Seite) | `session_tag_store.dart`, Drift `session_tags` (v12) | Done (Frontend-MS05) |
| Garlic forward path | `FlaschenpostV2.java` / `GarlicRouter.java` (MS04) | Done |
| RGB builder + reply flow | `rgb_builder.dart`, `reverse_garlic_block.dart`, `garlic_builder.dart` (tagged), `redpanda_light_client.dart` | Done (Frontend-MS05) |

## Spec

> **Hinweis (2026-06-13):** Die Abschnitte 1–4 beschreiben das ursprüngliche SURB-artige
> Modell mit von Alice **vorverschlüsselten** `encrypted_layers`. Das ist mit stateless
> MS04-Relays nicht umsetzbar (keine per-Hop-Payload-Transformation) und wurde durch das
> Hop-Deskriptor-Modell ersetzt — verbindlich ist
> [Decision 6](#decisions-backend-ms05-2026-06-13). Session-Tags (Abschnitt 5), Expiry
> (Abschnitt 6) und die Privacy-Ziele gelten weiter, mit dem in Decision 6 dokumentierten
> Tradeoff.

### 1. RGB Data Model

```
ReverseGarlicBlock {
  version: uint8           // 1
  expiry_ts: int64          // Unix ms — when this RGB expires
  session_tag: bytes[16]    // Random tag Alice uses to identify which channel/conversation this reply belongs to
  first_hop: KademliaId    // The first relay in the return path (H1)
  encrypted_layers: bytes   // Pre-encrypted onion layers (H1 → H2 → H3 → Alice's OH)
}
```

### 2. RGB Construction (Alice, Sender of Original Message)

Alice builds an RGB that Bob can use to reply:

```
Return path: Bob → H1' → H2' → H3' → Alice's OH

Alice picks 3 return-path hops (H1', H2', H3') — different from forward-path hops.

Layer 3 (innermost, for H3'):
  plaintext_3 = [CMD_DELIVER][20 Alice_OH_kademlia_id][16 session_tag][padding]
  layer_3 = encrypt(H3'.enc_pub, plaintext_3)

Layer 2 (for H2'):
  plaintext_2 = [CMD_FORWARD][20 H3'_kademlia_id][layer_3 + padding]
  layer_2 = encrypt(H2'.enc_pub, plaintext_2)

Layer 1 (outermost, for H1'):
  plaintext_1 = [CMD_FORWARD][20 H2'_kademlia_id][layer_2 + padding]
  layer_1 = encrypt(H1'.enc_pub, plaintext_1)

RGB.encrypted_layers = layer_1
RGB.first_hop = H1'_kademlia_id
```

### 3. RGB Inclusion in Outgoing Messages

When Alice sends a message to Bob, the channel-encrypted payload includes:

```
ChannelMessage {
  message_id: bytes
  content: bytes            // actual message text
  rgb: ReverseGarlicBlock   // pre-built reply path
  timestamp: int64
}
```

Alice attaches a fresh RGB to every message (or every N messages). Each RGB is single-use — once Bob uses it, Alice must provide a new one.

### 4. Using an RGB (Bob Replies)

When Bob wants to reply to Alice:

1. Take the most recent unused RGB from Alice.
2. Encrypt the reply with `K_enc` (channel encryption key).
3. Build a Flaschenpost v2 packet:
   - `next_hop` = `RGB.first_hop`
   - `encrypted_payload` = `[Bob's encrypted reply][RGB.encrypted_layers]`
4. Send the packet to `RGB.first_hop` (via Bob's own garlic forward path or directly if connected).

Each relay along the return path peels its layer (same as MS04 forward path), eventually delivering to Alice's OH with the `session_tag` attached.

### 5. Session Tags

Session tags allow Alice to correlate incoming replies with conversations:

- Alice generates a random 16-byte `session_tag` per RGB.
- Alice stores `session_tag → channel_id` in a local lookup table (Drift).
- When Alice fetches a message from her OH and it contains a `session_tag`, she looks up the corresponding channel and decrypts with that channel's `K_enc`.
- Session tags are single-use: after receiving a reply with a given tag, Alice removes it from the lookup.

### 6. RGB Expiry and Rotation

- Each RGB has an `expiry_ts` (e.g. 24 hours from creation).
- If Bob hasn't replied before expiry, the RGB is useless (the return-path hops may have rotated keys).
- Alice should include a fresh RGB in every message to ensure Bob always has a valid return path.
- If Alice changes her OH (e.g. moves to a different full node), old RGBs pointing to the old OH become invalid.

### 7. Privacy Properties

- Bob never learns Alice's OH node or network location — he only sees `RGB.first_hop`.
- Each relay on the return path only sees the next hop.
- The session tag is opaque to relays (it's inside the innermost encrypted layer).
- Different RGBs use different hops, preventing long-term path correlation.

## Protobuf Changes

```protobuf
// New message in commands.proto or a new rgb.proto:
message ReverseGarlicBlock {
  uint32 version = 1;
  int64 expiry_ts = 2;
  bytes session_tag = 3;       // 16 bytes
  bytes first_hop = 4;         // 20-byte KademliaId
  bytes encrypted_layers = 5;  // Pre-encrypted onion layers
}

// Extend ChannelMessage (from MS01):
message ChannelMessage {
  bytes message_id = 1;
  bytes content = 2;
  bytes iv = 3;
  int64 timestamp = 4;
  ReverseGarlicBlock reply_path = 5;  // NEW
}
```

## Backend Changes

| File | Action |
|------|--------|
| `GarlicRouter.java` (from MS04) | Handle `CMD_DELIVER` with session_tag: include tag in mailbox deposit |
| `OutboundMailboxStore.java` | Store session_tag alongside `MailItem` payload |
| No other backend changes | Relays are stateless — they just peel and forward, same as MS04 |

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `rgb_builder.dart` | Construct ReverseGarlicBlocks (pick hops, build layers) |
| **New**: `session_tag_store.dart` | Map session_tag → channel_id, backed by Drift |
| `garlic_builder.dart` (from MS04) | Include RGB in outgoing `ChannelMessage` |
| `redpanda_light_client.dart` | On fetch: extract session_tag, look up channel, decrypt |
| `database.dart` | Add `session_tags` table: `tag BLOB PK`, `channel_id TEXT`, `created_at DATETIME` |
| `channel.dart` | Add method to generate RGB for this channel |

## Acceptance Criteria

- [x] Alice can build an RGB and include it in an outgoing message *(`rgb_builder.dart`, `ChannelMessage.reply_path`; Unit + E2E)*
- [x] Bob can use the RGB to send a reply without knowing Alice's OH node *(E2E: Bob ohne `peerOhId`; die `oh_id` der Mailbox kennt er per Decision 6 — bewusster Tradeoff)*
- [x] The reply traverses 3 hops and arrives at Alice's OH *(Backend `ReverseGarlicRouterTest`; Frontend-E2E gegen das Referenz-JAR, 4 Nodes)*
- [x] Alice correlates the reply to the correct channel via session_tag *(`fetchMessages()`-Tag-Lookup, `DecryptedMessage.viaSessionTag`)*
- [x] Each RGB is single-use — reusing it fails or is detected *(Endpunkt-Disziplin per Decision 5: Tag-Consume beim Empfänger, RGB-Consume beim Antwortenden; Replays mit verbrauchtem Tag werden verworfen — Unit-getestet)*
- [x] Expired RGBs are rejected (message dropped, not delivered) *(clientseitig per Decision 5: Bob verwirft abgelaufene RGBs vor dem Senden und nutzt den Fallback; Alice bereinigt Tags nach 48 h)*
- [x] No relay on the return path can determine both Bob's identity and Alice's OH *(Reply = normale MS04-Onion: Relays sehen nur den next_hop, der Tag liegt in der innersten Schicht)*
- [x] A two-way conversation works: Alice sends with RGB, Bob replies via RGB, Alice sends again with a new RGB, etc. *(E2E: dritte Nachricht reist über Bobs Gegen-RGB)*

## Decisions (Backend-MS05, 2026-06-13)

Umgesetzt in redpandaj [#226](https://github.com/redPanda-project/redpandaj/pull/226), aufbauend auf den [Decisions (Backend-MS04)](ms04_multi_hop_garlic.md#decisions-backend-ms04-2026-06-12). Folgende Festlegungen sind **für Frontend MS05 verbindlich**:

1. **Neues Layer-Command `CMD_DELIVER_TAGGED (0x03)`** statt In-Place-Änderung von `CMD_DELIVER`:
   `[1 cmd][20 oh_id][16 session_tag][4 payload_len][payload][opt. Padding]`. `oh_id` ist
   **20 Bytes** (KademliaId, wie MS04 Decision 4 — die 32 im Pseudo-Code der Backend-View waren
   derselbe bekannte Fehler), `payload_len` explizit. `CMD_DELIVER (0x02)` bleibt byte-identisch,
   released Frontend-MS04-Clients bleiben kompatibel.
2. **`MailItem.session_tag` (Feld 5, optional)**: 16 Bytes oder leer (direkte/ungetaggte
   Nachrichten — Backend-View OQ 1 → optional). Wird unverändert in `FetchResponse` geliefert.
   Deposit-Validierung: leer oder exakt 16 Bytes, sonst `BAD_REQUEST`.
3. **`FlaschenpostPut.session_tag` (Feld 5)**: Der MS02b-Fallback (letzter Garlic-Hop ist nicht
   der OH-Host, MS04 Decision 9) konserviert den Tag auf dem Forward zum Host-Node.
   Bestandsclients setzen das Feld nie (leer = ungetaggt).
4. **Relays unverändert, OH-Node herkunftsagnostisch**: Kein Reverse-Sonderfall im Peeling;
   der OH-Node unterscheidet Forward/Reverse nicht (Backend-View OQ 2 → irrelevant, die
   Tag-Präsenz genügt dem Client zur Zuordnung).
5. **Single-Use & Expiry sind Client-Sache** (Relays bleiben stateless): Die packet_id-Dedup
   (5-Minuten-Fenster, MS04 Decision 7) stoppt byte-identische Replays eines Reply-Pakets.
   RGB-Single-Use erzwingt Alice (Tag nach Empfang aus dem Lookup entfernen; Replies mit
   unbekanntem/verbrauchtem Tag verwerfen), Expiry erzwingt Bob (abgelaufene RGBs nicht
   verwenden). Eine relay-seitige Tag-/Expiry-Prüfung würde die Stateless-Eigenschaft brechen
   und Tags gegenüber Relays offenlegen — bewusst nicht gebaut (KISS).
6. **RGB-Inhaltsmodell (ersetzt „encrypted_layers")**: Von Alice **vorverschlüsselte**
   Onion-Layers (SURB-artig) können Bobs Reply-Payload mit stateless MS04-Relays nicht
   transportieren — jede Layer ist GCM-authentifiziert, nachträglich lässt sich keine Payload
   einfügen; Sphinx/I2P lösen das mit per-Hop-Payload-Transformation, also genau der
   Relay-Änderung, die die Backend-View ausschließt. Der RGB enthält daher **Hop-Deskriptoren**
   statt vorverschlüsselter Layers:
   `{version, expiry_ts, session_tag, oh_id, hops[]: (kademlia_id 20 B, encryption_pub 32 B)}`,
   channel-verschlüsselt in der ChannelMessage (nur Bob liest ihn; Proto-Layout = Frontend-MS05).
   Bob baut die Reply als **Standard-MS04-Onion** über die von Alice gewählten Rückweg-Hops
   (clientseitiger `GarlicBuilder` wird wiederverwendet), innerste Schicht =
   `CMD_DELIVER_TAGGED`. Privacy-Tradeoff gegenüber der ursprünglichen Spec: Bob kennt Alices
   `oh_id` und die Rückweg-Hops — die `oh_id` kennt er im heutigen Channel-Setup ohnehin
   (`peerOhEndpoint`, Frontend-MS04 Decision 6). Relays sehen weiterhin nur den next_hop, der
   Tag bleibt in der innersten Schicht; das OH-Verstecken vor dem Channel-Partner ist auf einen
   späteren Milestone verschoben (bräuchte Sphinx-artige Reply-Blöcke).
7. **Byte-Budget (Spike-Deliverable, Teil 1)**: äußerste Schicht max. 1959 B Plaintext; je
   FORWARD-Schicht −85 B; `CMD_DELIVER_TAGGED`-Overhead 41 B (1+20+16+4). Bei 3 Hops
   (2× FORWARD + 1× DELIVER_TAGGED): **max. 1748 B Reply-Payload** (16 B weniger als MS04).
   Der RGB selbst (~13 B Proto-Gerüst + 16 Tag + 20 oh_id + 3×52 Hop-Deskriptoren ≈ 205 B)
   reist in Alices ChannelMessage und passt bequem ins MS04-Budget (~1,65 KiB Content).
8. **Attack-Class-Mapping (Spike-Deliverable, Teil 2)**: *Tagging* — Reply ist eine normale
   Sender-gebaute Onion, es existiert kein malleables vorverschlüsseltes Material; jede Layer
   ist GCM-authentifiziert mit AAD = next_hop, manipulierte Pakete sterben am nächsten Hop.
   *Replay* — packet_id-Dedup am Relay (5 min) + Single-Use-Tags am Endpunkt (Decision 5);
   Langzeit-Replays liefern höchstens in eine Mailbox, deren Client den Tag bereits verworfen
   hat. *Size correlation* — fixe 2048-B-Pakete; das ct_len-Schrumpfen pro Hop bleibt wie in
   MS04 Decision 5 akzeptiert (Mitigation = Dummy-Traffic, deferred).

## Decisions (Frontend-MS05, 2026-07-02)

Umgesetzt in redpanda-mobile [#33](https://github.com/redPanda-project/redpanda-mobile/pull/33), aufbauend auf den [Decisions (Backend-MS05)](#decisions-backend-ms05-2026-06-13):

1. **RGB-Proto-Layout (Decision 6 konkretisiert)**: hand-gerolltes proto3-kompatibles
   Binärformat in `domain/reverse_garlic_block.dart` (wie `channel_message.dart`; die
   generierten Proto-Dateien bleiben hand-gepflegt): `version=1 (uint32), expiry_ts=2
   (int64), session_tag=3 (16 B), oh_id=4 (20 B), hops=5 (repeated {kad_id=1 20 B,
   enc_pub=2 32 B})`. Eingebettet als `ChannelMessage.reply_path = 5` (Feldnummer aus der
   Master-Spec; Feld 4 bleibt frei). Striktes Parsen: Version ≠ 1, falsche Feldlängen oder
   0 Hops ⇒ `FormatException`, die RGB wird ignoriert. Real-Größe bei 3 Hops: **223 B**
   (Decision 7 schätzte ~205 B — passt weiterhin bequem).
2. **Master-OQ 2 (Batch-Größe) → 1 frische RGB pro Nachricht, keine Batches** (KISS,
   Spec-Default). Der Empfänger hält pro Channel nur die **neueste** unverbrauchte RGB
   (`Channels.pendingRgb` statt einer `received_rgbs`-Tabelle): jede eingehende Nachricht
   ersetzt sie, ältere hätten nur kürzere Restlaufzeit. Schnelle Mehrfach-Replies nutzen
   die RGB für die erste Reply und den Fallback (Decision 3) für die weiteren, bis eine
   neue RGB eintrifft.
3. **Master-OQ 3 (Expiry-Fallback) → Forward-Pfad**: ist die pending RGB abgelaufen (oder
   keine vorhanden / Payload über dem Tagged-Budget von 1748 B), wird sie verworfen und
   die Nachricht reist den normalen MS04-Pfad (3-Hop-Garlic via `peerOhId`, notfalls
   MS02b-Direkt-Deposit). Ungetaggt — die Zuordnung läuft dann wie bisher über das
   Channel-OH.
4. **Reply-Bau = shared `GarlicBuilder`**: `GarlicBuilder.build(..., sessionTag:)` erzeugt
   die innerste `CMD_DELIVER_TAGGED`-Schicht; die RGB-Hops werden exakt in
   Deskriptor-Reihenfolge traversiert. Kein separater Reply-Pfad-Code, Budget-Guard
   `maxPayloadLength(hops, tagged: true)`.
5. **RGB-Hop-Wahl der Ausstellerin**: gleicher `HopSelector` wie MS04 (frische
   Zufallsauswahl pro RGB, Präfix-Diversität), Ausschluss = eigener OH-Host-Endpoint
   (Analogon zur `peerOhEndpoint`-Regel). **Keine erzwungene Disjunktheit** zu den
   Forward-Hops (KISS — in kleinen Netzen unmöglich; unabhängige Zufallswahl pro RGB
   erfüllt „different RGBs use different hops" statistisch). Degradierung wie MS04:
   weniger Kandidaten ⇒ kürzerer Rückpfad + Log-Warnung; 0 Kandidaten ⇒ Nachricht ohne
   RGB.
6. **Single-Use & Tag-Hygiene (Decision 5 umgesetzt)**: Tags mit Nachricht ausgegeben,
   beim Fetch werden Items mit unbekanntem/verbrauchtem/fremdem Tag **vor** dem Decrypt
   verworfen; konsumiert wird ein Tag erst nach erfolgreichem Decrypt (transiente
   Re-Deliveries nach AckFetch-Fehlern bleiben lesbar, Ciphertext-Replays scheitern am
   Ratchet). Ausstehende Tags > 48 h werden im Polling-Zyklus bereinigt (RGB-Lifetime
   24 h + Slack). `DecryptedMessage.viaSessionTag` macht den Reverse-Empfang für
   App/Tests sichtbar.
7. **Persistenz im Ratchet-Muster statt eigener Provider**: `SessionTagStore` und pending
   RGB leben im Netzwerk-Isolate; Änderungen werden als `GarlicSessionUpdate`-Snapshots
   (Tags + pendingRgb je Channel) emittiert und vom `MessageSyncService` in Drift **v12**
   persistiert (`session_tags`-Tabelle: `tag` TEXT hex PK, `channel_id`, `created_at`;
   Spalte `Channels.pendingRgb`; nicht destruktiv). Restore über
   `addChannelKeys(sessionTags:, pendingRgbHex:)`, nur bei der ersten Registrierung
   (Live-State gewinnt). Die in der alten Frontend-View skizzierten
   `sessionTagStoreProvider`/`rgbBuilderProvider` entfallen — die Stores sind im
   Isolate-Client gekapselt. Verlorene Tags würden Replies stumm verwerfen, daher ist die
   Persistenz Pflicht; eine verlorene pending RGB heilt sich mit der nächsten Nachricht.
8. **`MailItem.sessionTag` (Feld 5)** im hand-erweiterten `commands.pb.dart` ergänzt;
   `FlaschenpostPut.session_tag` braucht der Client nicht (setzt nur der Server beim
   MS02b-Fallback) und bleibt clientseitig ungeneriert.

## Open Questions

Alle beantwortet — Backend: [Decisions (Backend-MS05)](#decisions-backend-ms05-2026-06-13), Frontend: [Decisions (Frontend-MS05)](#decisions-frontend-ms05-2026-07-02):

1. ~~Should RGBs be single-use (maximum privacy) or reusable within a session (simpler)?~~ → Single-use, erzwungen am Endpunkt (Backend-Decision 5, Frontend-Decision 6); Relays prüfen nichts.
2. ~~How many RGBs should Alice pre-generate and send to Bob? One per message, or a batch?~~ → Eine frische RGB pro Nachricht, keine Batches; nur die neueste wird gehalten (Frontend-Decision 2).
3. ~~What happens if all of Bob's RGBs for Alice expire?~~ → Fallback auf den Forward-Pfad (MS04-Garlic via `peerOhId`/Direkt-Deposit), ungetaggt (Frontend-Decision 3).
4. ~~Should the RGB include a reply encryption key, or rely on channel `K_enc`?~~ → Channel-Krypto (Ratchet/Envelope v4, MS03b) bleibt zuständig; der RGB transportiert nur Routing-Infos + Tag (Decision 6, KISS).
5. ~~How large can an RGB be before it makes the ChannelMessage too big?~~ → 223 B bei 3 Hops (Frontend-Decision 1), unkritisch (Decision 7).
