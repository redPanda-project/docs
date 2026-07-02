# Frontend MS05: RGB Builder & Session Tags

## Status: Done (2026-07-02, mobile [#33](https://github.com/redPanda-project/redpanda-mobile/pull/33))

> **Backend-Abhängigkeit**: [Backend MS05](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms05_reverse_garlic.md) Done (redpandaj [#226](https://github.com/redPanda-project/redpandaj/pull/226)) —
> `CMD_DELIVER_TAGGED (0x03)`, `MailItem.session_tag` via `FetchResponse`, `FlaschenpostPut.session_tag` für den MS02b-Fallback.

## Goal

Alice baut Reverse Garlic Blocks (RGBs) und hängt sie an ausgehende Nachrichten an. Bob nutzt das RGB, um Replies an Alice zu senden, ohne Alices OH-Node zu kennen. Alice korreliert eingehende Replies über Session-Tags mit dem richtigen Channel.

> **Modellwechsel (verbindlich, Master-Spec [Decision 6](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms05_reverse_garlic.md#decisions-backend-ms05-2026-06-13)):**
> Der ursprüngliche Spec-Entwurf (von Alice **vorverschlüsselte** Onion-Layers, SURB-artig)
> ist mit den stateless MS04-Relays nicht payload-fähig. Der RGB enthält stattdessen
> **Hop-Deskriptoren**; Bob baut die Reply als Standard-MS04-Onion mit innerster Schicht
> `CMD_DELIVER_TAGGED` über den shared `GarlicBuilder`. Die Pseudo-Code-Abschnitte der
> alten View-Fassung sind durch die implementierte Form unten ersetzt.

## Prerequisites

- Frontend MS04 Done — Garlic-Builder, Hop-Selektion
- Backend MS05 Done — `MailItem.session_tag` in Mailbox gespeichert und via Fetch zurückgegeben

## Current State

| Component | File | Status |
|-----------|------|--------|
| Garlic Builder | `garlic/garlic_builder.dart` | Done — forward path (MS04) **und** tagged Reverse-Replies (`sessionTag`-Parameter, MS05) |
| RGB Modell + Codec | `domain/reverse_garlic_block.dart` | Done |
| RGB Builder | `garlic/rgb_builder.dart` | Done |
| Session Tags | `garlic/session_tag_store.dart` + Drift `session_tags` | Done |
| Persistenz/Restore | `message_sync_service.dart`, Drift v12 | Done |

## Spec (implementiert)

### 1. RGB Data Model & Codec

**`domain/reverse_garlic_block.dart`** — hand-gerolltes proto3-kompatibles Binärformat
(wie `channel_message.dart`, die generierten Proto-Dateien werden nicht regeneriert):

```protobuf
ReverseGarlicBlock {
  uint32 version     = 1;  // 1
  int64  expiry_ts   = 2;  // Unix ms, Erstellung + 24 h
  bytes  session_tag = 3;  // 16 Random-Bytes
  bytes  oh_id       = 4;  // 20-Byte KademliaId von Alices OH-Mailbox
  repeated RgbHop hops = 5;
}
RgbHop {
  bytes kad_id  = 1;  // 20-Byte KademliaId des Relays
  bytes enc_pub = 2;  // 32-Byte X25519 Encryption Public Key
}
```

Validierung bei Konstruktion/Deserialisierung: Version exakt 1, Tag exakt 16 B, oh_id exakt
20 B, ≥ 1 Hop, Hop-Feldlängen via `GarlicHop`. 3-Hop-RGB serialisiert = **223 B** (die ~205 B
aus Master-Decision 7 waren eine Schätzung; passt weiter bequem ins ChannelMessage-Budget).

### 2. RGB Builder

**`garlic/rgb_builder.dart`**: wählt bis zu 3 Return-Hops über den shared `HopSelector`
(Ausschluss: der eigene OH-Host-Endpoint, analog zur MS04-`peerOhEndpoint`-Regel), frischer
16-Byte-Tag (`CryptoUtils.randomBytes`), `expiry_ts = now + 24 h` (`RgbBuilder.rgbLifetime`).
Ohne eligible Kandidaten → `null` (Nachricht reist ohne Reply-Path, Log-Hinweis).

### 3. RGB in ausgehenden Nachrichten

`sendMessage()` hängt **eine** frische RGB an jede Nachricht, sobald der Channel ein
eigenes OH besitzt (`ChannelMessage.reply_path = 5`, channel-/ratchet-verschlüsselt).
Der Tag wird vor dem Senden im `SessionTagStore` registriert und als
`GarlicSessionUpdate`-Snapshot an die App-Schicht emittiert (Persistenz).

### 4. Reply senden (Bob)

`sendMessage()` prüft zuerst die **pending RGB** des Channels:

- gültig (nicht expired, Payload ≤ 1748 B @ 3 Hops) → Standard-MS04-Onion über
  `rgb.hops` mit innerster Schicht `CMD_DELIVER_TAGGED` an `rgb.oh_id`
  (`GarlicBuilder.build(..., sessionTag: rgb.sessionTag)`), Submit via Command 142;
  danach wird die RGB konsumiert (single-use).
- expired → verwerfen + Fallback auf den Forward-Pfad (MS04-Garlic bzw. Direkt-Deposit).

Bob braucht **kein** `peerOhId` — die RGB ist seine einzige Route zurück (E2E-getestet).

### 5. Session-Tag Store

**`garlic/session_tag_store.dart`** (im Netzwerk-Isolate, in-memory):
`store/lookup/consume` (single-use) + `cleanup()` (Tags > 48 h). Persistiert wird über
`GarlicSessionUpdate`-Snapshots in die Drift-Tabelle `session_tags`
(`tag` TEXT hex PK, `channel_id`, `created_at`); Restore beim App-Start via
`addChannelKeys(sessionTags:, pendingRgbHex:)` — wie beim Ratchet-State gilt: Live-State
gewinnt, Restore nur bei der ersten Registrierung des Channels.

### 6. Eingehende Messages mit Session-Tag

`fetchMessages()`: Items mit nicht-leerem `session_tag` werden **vor** dem Entschlüsseln
gegen den Store geprüft — unbekannte/verbrauchte/fremde Tags ⇒ Item verworfen
(Single-Use-Disziplin, Master-Decision 5). Konsumiert wird der Tag erst nach erfolgreichem
Decrypt (transient nicht entschlüsselbare Items werden re-delivered und finden ihren Tag
noch; Ciphertext-Replays scheitern am Ratchet und können verbrauchte Tags nicht
wiederbeleben). Enthaltene `reply_path`-RGBs werden als neue pending RGB übernommen
(nur die neueste pro Channel). `DecryptedMessage.viaSessionTag` markiert getaggte Replies.

### 7. RGB Rotation & Lifecycle

- Jede ausgehende Nachricht trägt eine frische RGB (Master-OQ 2 → 1 pro Nachricht).
- Pending RGBs sind single-use und werden durch jede neuere ersetzt.
- Session-Tags > 48 h werden im Polling-Zyklus bereinigt.

### 8. Database Migration

**Drift v12 (nicht destruktiv):**
- Neue Tabelle `session_tags`: `tag` TEXT (hex) PK, `channel_id` TEXT → Channels, `created_at` DATETIME
- Neue Spalte `Channels.pendingRgb` TEXT (hex-serialisierte RGB) — eine `received_rgbs`-Tabelle
  entfällt, da pro Channel nur die neueste RGB aufbewahrt wird

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `domain/reverse_garlic_block.dart` | RGB-Modell + proto3-kompatibler Codec |
| **New**: `garlic/rgb_builder.dart` | RGBs bauen (Hop-Deskriptoren, Tag, Expiry) |
| **New**: `garlic/session_tag_store.dart` | session_tag → channel_id, single-use |
| **New**: `domain/garlic_session_update.dart` | Persistenz-Snapshots für die App-Schicht |
| `garlic/garlic_builder.dart` | `CMD_DELIVER_TAGGED`-Layer, `maxPayloadLength(tagged:)` |
| `crypto/channel_message.dart` | `reply_path`-Feld (5) |
| `generated/commands.pb.dart` | `MailItem.sessionTag` (Feld 5, hand-erweitert) |
| `client/redpanda_light_client.dart` | RGB in `sendMessage()`, Tag-Korrelation in `fetchMessages()`, Cleanup |
| `client_facade.dart`, `isolate_*.dart`, `mock_redpanda_client.dart` | `garlicSessionUpdates`-Stream + Restore-Parameter |
| `database/database.dart` | Migration v12: `session_tags` + `Channels.pendingRgb` |
| `services/message_sync_service.dart` | Snapshot-Persistenz + Restore |

## Acceptance Criteria

- [x] Alice baut ein RGB und hängt es an ihre Nachricht an *(Unit `ms05_reverse_garlic_test.dart`, E2E)*
- [x] Bob empfängt das RGB und kann damit eine Reply senden *(ohne `peerOhId`, E2E)*
- [x] Die Reply traversiert 3 Hops und landet in Alices OH-Mailbox *(E2E gegen Referenz-JAR, 4 Nodes)*
- [x] Alice korreliert die Reply über den Session-Tag zum richtigen Channel *(`viaSessionTag`, Channel-Match)*
- [x] Jedes RGB ist single-use — nach Verbrauch wird der Session-Tag gelöscht *(Unit: Replay/Doppel-Fetch verworfen)*
- [x] Abgelaufene RGBs (>24h) werden nicht akzeptiert *(Bob verwirft + Fallback, Unit-getestet)*
- [x] Bob kennt zu keinem Zeitpunkt Alices OH-Node oder IP-Adresse *(kein `peerOhId` bei Bob; `oh_id` der Mailbox kennt er per Decision 6 — bewusster Tradeoff)*
- [x] Zwei-Wege-Konversation funktioniert: Alice→Bob (Garlic), Bob→Alice (RGB), Alice→Bob (neues Garlic mit neuem RGB) *(E2E: dritte Nachricht reist über Bobs Gegen-RGB)*

## Open Questions

Beantwortet — siehe [Decisions (Frontend-MS05)](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms05_reverse_garlic.md#decisions-frontend-ms05-2026-07-02) in der Master-Spec:

1. ~~Wie wird der Reply-Payload in das pre-encrypted RGB eingesetzt?~~ → Obsolet; Bob baut die Onion selbst (Master-Decision 6).
2. ~~Batch von RGBs pro Nachricht?~~ → Nein, 1 frische RGB pro Nachricht (Frontend-Decision 2).
3. ~~Fallback nach Expiry?~~ → Forward-Pfad (MS04-Garlic/Direkt-Deposit), Frontend-Decision 3.
4. ~~Maximale RGB-Größe?~~ → 223 B bei 3 Hops, unkritisch (Master-Decision 7).
