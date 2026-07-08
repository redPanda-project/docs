# Frontend MS06: ACK Handling & Node Scoring

## Status: Done (2026-07-08, mobile [#37](https://github.com/redPanda-project/redpanda-mobile/pull/37))

> **Backend-Abhängigkeit**: [Backend MS06](../backend/ms06_two_layer_ack.md) ist **Done** (redpandaj [#229](https://github.com/redPanda-project/redpandaj/pull/229)).
> Verbindlich waren die [Decisions (Backend-MS06)](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms06_two_layer_ack.md#decisions-backend-ms06-2026-07-03):
> `CMD_DELIVER_ACKED (0x04)` mit Hop-Deskriptor-Return-Path (kein pre-encrypted Block!),
> `RoutingAck {timestamp_ms, status}` **ohne** `message_id` — die Korrelation läuft über den
> `ack_session_tag`, der als `MailItem.session_tag` am R-ACK-Item hängt. Die
> Pseudocode-Abschnitte unten stammen aus der Zeit vor den Backend-Decisions; die
> tatsächliche Umsetzung folgt den Decisions und ist in den [Decisions (Frontend-MS06)](#decisions-frontend-ms06-2026-07-08) am Ende festgehalten.

## Goal

R-ACKs empfangen und Message-Status aktualisieren. Channel-ACKs (Lesebestätigungen) senden. Node-Scoring basierend auf R-ACK-Daten für bessere Hop-Selektion. Chat UI zeigt detaillierten Zustellstatus.

## Prerequisites

- Frontend MS05 Done — RGBs + Session-Tags funktionieren
- Backend MS06 Done — OH-Node generiert R-ACKs

## Current State

| Component | File | Status |
|-----------|------|--------|
| Message Status | `database.dart` → `Messages.status` | Done — 0=pending, 1=sent, 2=routed, 3=delivered, 5=failed |
| Return-Path-Bau | `garlic/return_path.dart`, `garlic/garlic_builder.dart` | Done — `ReturnPathBlock`, `CMD_DELIVER_ACKED` (0x04), `maxAckedPayloadLength` |
| R-ACK Handling | `garlic/ack_tag_store.dart`, `client/redpanda_light_client.dart`, `services/message_sync_service.dart` | Done — Tag-Korrelation, `routed`-Status, Timeout-Requeue |
| Channel-ACK | `crypto/channel_message.dart` (Feld 6), `client/redpanda_light_client.dart` | Done — Auto-ACK bei Empfang, `delivered`-Status |
| Node Scoring | `garlic/node_scorer.dart`, `garlic/hop_selector.dart`, `database.dart` (v13 `node_scores`) | Done — Score + Jitter, persistiert |

## Spec

### 1. Return-Path Construction

> **Überholt durch Backend-MS06 Decision 1–2:** Der Return-Path ist **kein** pre-encrypted
> Block, sondern trägt Hop-Deskriptoren (wie die RGB, MS05 Decision 6) — der ackende Node
> baut die R-ACK-Onion selbst. Der Client serialisiert nur:
> `[20 ack_oh_id][16 ack_session_tag][1 hop_count 0..4][hop_count × (20 kad_id + 32 enc_pub)]`
> und sendet mit dem Layer-Command `CMD_DELIVER_ACKED (0x04)`:
> `[1 cmd][20 oh_id][1 tag_len (0|16)][tag][return_path][4 payload_len][payload]`.

Beim Senden einer Nachricht wählt der Client Return-Hops (gleicher `HopSelector` wie
MS04/RGB), erzeugt einen frischen `ack_session_tag` pro Nachricht, merkt ihn sich als
Mapping `tag → message_id` und hängt den Deskriptor-Block an den `CMD_DELIVER_ACKED`-Layer.
Budget-Achtung: mit Tag + 3 Return-Hops sinkt das Payload-Maximum auf **1554 B** bei 3
Forward-Hops (Backend-MS06 Decision 6).

### 2. R-ACK Empfang

R-ACKs kommen als reguläre MailItems in Alices OH-Mailbox an (via Return-Path).

> **Überholt durch Backend-MS06 Decision 3:** Es gibt **kein Type-Byte** im Payload und
> kein `message_id`-Feld im `RoutingAck` — erkannt und korreliert wird über
> `item.session_tag`: steht der Tag im lokalen `ack_session_tag → message_id`-Mapping,
> ist das Item ein R-ACK und der Payload parst als `RoutingAck {timestamp_ms, status}`.
> (Die MS05-Tag-Hygiene gilt analog: unbekannte/verbrauchte Tags verwerfen.)

**`fetchMessages()` — R-ACK Erkennung:**

```dart
for (final item in response.items) {
  // R-ACK? Der Session-Tag des Items steht im lokalen Ack-Tag-Mapping.
  final pendingAck = ackTagStore.lookup(item.sessionTag);
  if (pendingAck != null) {
    final rAck = RoutingAck.fromBuffer(item.payload);
    await handleRAck(pendingAck.messageId, rAck);
    continue;
  }

  // Reguläre Nachricht / RGB-Reply verarbeiten...
}
```

**`handleRAck()`:**

```dart
Future<void> handleRAck(RoutingAck rAck) async {
  // 1. Message-Status updaten
  switch (rAck.status) {
    case 0x00: // STORED
      await db.updateMessageStatus(rAck.messageId, MessageStatus.routed); // 2
      break;
    case 0x01: // MAILBOX_FULL
      log.warning('Recipient mailbox full for message ${rAck.messageId}');
      // Optional: User benachrichtigen
      break;
    case 0x02: // HANDLE_EXPIRED
      log.warning('Recipient OH expired for message ${rAck.messageId}');
      await db.updateMessageStatus(rAck.messageId, MessageStatus.failed); // 5
      break;
  }

  // 2. Latenz messen (sendTime → rAck.timestamp)
  final message = await db.getMessageByMessageId(rAck.messageId);
  if (message != null) {
    final latencyMs = rAck.timestamp - message.timestamp.millisecondsSinceEpoch;
    await nodeScorer.recordSuccess(message.routedViaHops, latencyMs);
  }
}
```

### 3. Channel-ACK senden

Wenn Bob eine Nachricht empfängt und liest, sendet er einen Channel-ACK an Alice (via RGB):

```dart
Future<void> sendChannelAck(Channel channel, ReverseGarlicBlock rgb, Uint8List messageId) async {
  final ackMsg = ChannelMessage()
    ..messageId = generateMessageId()
    ..ack = (ChannelAck()
      ..messageId = messageId
      ..timestamp = DateTime.now().millisecondsSinceEpoch)
    ..timestamp = DateTime.now().millisecondsSinceEpoch;

  final encrypted = encryptChannelMessage(channel, ackMsg.writeToBuffer());
  final packet = insertPayloadIntoRGB(rgb, encrypted);
  await sendToNode(rgb.firstHop, packet);
}
```

### 4. Channel-ACK Empfang

**In `fetchMessages()` — ChannelMessage Parsing:**

```dart
final channelMsg = ChannelMessage.fromBuffer(decryptedPayload);

if (channelMsg.hasAck()) {
  // Lesebestätigung — Message-Status auf "delivered" setzen
  await db.updateMessageStatus(channelMsg.ack.messageId, MessageStatus.delivered); // 3
} else {
  // Reguläre Textnachricht
  await db.insertMessage(channelId, 'peer', utf8.decode(channelMsg.content));
}
```

### 5. Message Status Lifecycle (komplett)

```
0: pending     — lokal erstellt, noch nicht gesendet
1: sent        — an Netzwerk übergeben (Garlic-Paket gesendet)
2: routed      — R-ACK empfangen (in OH-Mailbox gespeichert)
3: delivered   — Channel-ACK empfangen (Empfänger hat die Nachricht)
4: read        — Read-ACK empfangen (optional, für später)
5: failed      — fehlgeschlagen nach max Retries
```

### 6. Node Scorer

**Neue Datei `garlic/node_scorer.dart`:**

```dart
class NodeScorer {
  final AppDatabase db;

  Future<void> recordSuccess(List<KademliaId> hops, int latencyMs) async {
    for (final hop in hops) {
      await db.updateNodeScore(hop,
        successDelta: 1,
        latencyMs: latencyMs,
      );
    }
  }

  Future<void> recordFailure(List<KademliaId> hops) async {
    for (final hop in hops) {
      await db.updateNodeScore(hop, failureDelta: 1);
    }
  }

  Future<double> getScore(KademliaId nodeId) async {
    final score = await db.getNodeScore(nodeId);
    if (score == null) return 0.5; // Unbekannt → neutral
    return score.successCount / (score.successCount + score.failureCount);
  }

  Future<List<NodeScore>> getTopNodes(int limit) async {
    return db.getNodeScoresSorted(limit);
  }
}
```

### 7. Hop Selector Integration

**`hop_selector.dart` — Score-basierte Auswahl:**

```dart
List<PeerInfo> selectHops(...) {
  final candidates = ...;

  // Nach Score sortieren (höchster zuerst)
  candidates.sort((a, b) {
    final scoreA = nodeScorer.getCachedScore(a.nodeId) ?? 0.5;
    final scoreB = nodeScorer.getCachedScore(b.nodeId) ?? 0.5;
    return scoreB.compareTo(scoreA);
  });

  // Top-Candidates mit etwas Zufall (nicht immer die gleichen 3)
  return weightedRandomSelection(candidates, count: 3);
}
```

### 8. R-ACK Timeout

```dart
// Periodischer Check (alle 60 Sekunden):
Timer.periodic(Duration(seconds: 60), (_) async {
  final unconfirmed = await db.getMessagesSentBefore(
    DateTime.now().subtract(Duration(seconds: 60)),
    status: MessageStatus.sent,
  );

  for (final msg in unconfirmed) {
    log.info('No R-ACK for message ${msg.id} after 60s');
    await nodeScorer.recordFailure(msg.routedViaHops);
    // Retry mit neuen Hops
    await retrySendWithNewHops(msg);
  }
});
```

### 9. Chat UI Status Icons

**`chat_screen.dart`:**

| Status | Icon | Farbe |
|--------|------|-------|
| 0 pending | Clock | Grey |
| 1 sent | Single arrow | Grey |
| 2 routed | Single checkmark | Grey |
| 3 delivered | Double checkmark | Blue |
| 5 failed | X mark | Red |

### 10. Database Migration

**Schema v10:**
- `Messages` Table: `routed_via_hops TEXT` (JSON array von KademliaId hex strings)
- Neue Table `node_scores`: `node_id TEXT PK`, `success_count INT`, `failure_count INT`, `avg_latency_ms INT`, `last_updated DATETIME`

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `garlic/node_scorer.dart` | Node-Scoring basierend auf R-ACK Daten |
| **New**: `ack/ack_handler.dart` | R-ACK + Channel-ACK Verarbeitung |
| `garlic/garlic_builder.dart` | Return-Path Construction für R-ACK |
| `garlic/hop_selector.dart` | Score-basierte Hop-Auswahl |
| `client/redpanda_light_client.dart` | R-ACK Timeout-Timer, Return-Path in Sends, Channel-ACK senden |
| `chat_screen.dart` | Detaillierte Status-Icons (pending/sent/routed/delivered/failed) |
| `database.dart` | Migration v10: `node_scores` Table, `routed_via_hops` Column |
| `providers.dart` | `nodeScorerProvider`, `ackHandlerProvider` |

## Acceptance Criteria

- [x] R-ACK empfangen → Message-Status wechselt von `sent` zu `routed`
- [x] Channel-ACK empfangen → Message-Status wechselt zu `delivered`
- [x] R-ACK mit `MAILBOX_FULL` → Re-Queue mit Backoff (statt nur Log)
- [x] R-ACK mit `HANDLE_EXPIRED` → Re-Queue über frische Hops (siehe Decision 5)
- [x] Kein R-ACK nach dem Timeout → Hops negativ gescored, Retry mit neuen Hops
- [x] Hop-Selektion bevorzugt höher-gescorte Nodes
- [x] Chat UI zeigt unterschiedliche Icons für jeden Status
- [x] Channel-ACK wird automatisch gesendet wenn Bob eine Nachricht empfängt
- [x] Node-Scores persistieren über App-Restarts (Drift v13)

## Decisions (Frontend-MS06, 2026-07-08)

Umgesetzt in mobile [#37](https://github.com/redPanda-project/redpanda-mobile/pull/37), aufbauend auf den [Decisions (Backend-MS06)](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms06_two_layer_ack.md#decisions-backend-ms06-2026-07-03). Sie beantworten die obigen Open Questions und ersetzen die veralteten Pseudocode-Passagen:

1. **R-ACK wird nur bei eigenem OH angefordert.** Der Sender hängt den `CMD_DELIVER_ACKED`-Return-Path nur an, wenn der Kanal eine eigene OH-Mailbox hat (nur dann existiert ein Ziel für die R-ACK). Return-Hops werden mit demselben `HopSelector` wie der Forward-/RGB-Pfad gewählt (eigener OH-Host und Submit-Node ausgeschlossen). Passt der Payload dann nicht mehr ins Acked-Budget (1554 B getaggt @ 3+3 Hops), degradiert der Sender auf das un-acked Format statt zu scheitern.
2. **R-ACK-Erkennung über den Ack-Tag (OQ 4 beantwortet).** Ein frischer `ack_session_tag` pro Nachricht wird lokal auf `message_id` + beteiligte Hops gemappt (`AckTagStore`, in-memory, single-use). Ein gefetchtes MailItem mit passendem Tag ist ein R-ACK — sein Payload parst als `RoutingAck`, **nie** channel-verschlüsselt. Das gilt genauso für RGB-Replies: auch die reverse-getaggte Zustellung trägt einen eigenen Return-Path, sofern der Antwortende einen eigenen OH hat.
3. **Channel-ACK automatisch bei Empfang (OQ 1).** Nach erfolgreichem Entschlüsseln einer regulären Nachricht sendet der Client einen leeren `ChannelMessage` mit `ack_message_id` (neues **Feld 6**, längen-delimitiert, wire-kompatibel) zurück — fire-and-forget über den Forward-Pfad, ohne die pending RGB zu verbrauchen und ohne selbst ein R-ACK anzufordern (keine ACK-für-ACK-Schleifen). Read-ACKs (Status 4) bleiben offen für später.
4. **Node-Scoring: Score + Jitter statt harter Sortierung (OQ 2).** `NodeScorer` führt Erfolg/Timeout pro Hop und einen laufenden Latenz-Schnitt. Der `HopSelector` verwirft Nodes unter 0,3 Zustellrate erst ab 3 Beobachtungen (ein einzelnes verpasstes R-ACK blacklistet nichts) und ordnet die übrigen nach `score + random·0,25`, damit Pfade divers bleiben. Ein R-ACK ist ein Hinweis, kein Beweis (Master-OQ 5) — deshalb kollektive Gutschrift/Abwertung aller beteiligten Hops. Persistenz: Drift v13 `node_scores`, Restore beim Start (Live-State gewinnt, wie Ratchet/Garlic-Session).
5. **Status-Semantik nur aufwärts, `failed`/Timeout re-queuen statt terminieren.** `routed`/`delivered` heben frühere Zustände (inkl. `failed`) an; ein spätes ACK kann eine bereits bestätigte Nachricht nicht zurücksetzen. Ausbleibendes R-ACK (Timeout 90 s = 3 Polling-Zyklen), `HANDLE_EXPIRED`, `MAILBOX_FULL` und `REJECTED` re-queuen die Nachricht über die MS02-Retry-Queue (frische Hops) — nur `sent`-Nachrichten, damit ein spätes ACK die Wiedervorlage nicht überholt. Der `AckTagStore` wird bewusst **nicht** persistiert: nach einem Neustart verfallen ausstehende Erwartungen, die Nachricht behält `sent` (pre-MS06-Semantik) statt fälschlich als Timeout zu gelten.
6. **Status-Icons (OQ 3):** `pending` Uhr · `sent` Pfeil ↑ · `routed` einzelnes Häkchen · `delivered` blaues Doppelhäkchen · `failed` rotes X — nur für eigene ausgehende Nachrichten. Der Detailgrad ist für Nutzer vertraut (WhatsApp-artig) und wurde beibehalten.
