# Frontend MS06: ACK Handling & Node Scoring

## Status: Missing (entblockt — Backend MS06 Done 2026-07-03)

> **Backend-Abhängigkeit**: [Backend MS06](../backend/ms06_two_layer_ack.md) ist **Done** (redpandaj [#229](https://github.com/redPanda-project/redpandaj/pull/229)) — dieser Milestone ist entblockt.
> Verbindlich sind die [Decisions (Backend-MS06)](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms06_two_layer_ack.md#decisions-backend-ms06-2026-07-03):
> `CMD_DELIVER_ACKED (0x04)` mit Hop-Deskriptor-Return-Path (kein pre-encrypted Block!),
> `RoutingAck {timestamp_ms, status}` **ohne** `message_id` — die Korrelation läuft über den
> `ack_session_tag`, der als `MailItem.session_tag` am R-ACK-Item hängt. Die
> Pseudocode-Abschnitte unten stammen aus der Zeit vor den Backend-Decisions und sind an
> diesen Stellen entsprechend zu lesen (siehe eingestreute Hinweise).

## Goal

R-ACKs empfangen und Message-Status aktualisieren. Channel-ACKs (Lesebestätigungen) senden. Node-Scoring basierend auf R-ACK-Daten für bessere Hop-Selektion. Chat UI zeigt detaillierten Zustellstatus.

## Prerequisites

- Frontend MS05 Done — RGBs + Session-Tags funktionieren
- Backend MS06 Done — OH-Node generiert R-ACKs

## Current State

| Component | File | Status |
|-----------|------|--------|
| Message Status | `database.dart` → `Messages.status` | 0=pending, 1=sent, 5=failed (aus MS02) |
| Node Scoring | — | Missing |
| R-ACK Handling | — | Missing |
| Channel-ACK | — | Missing |

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

- [ ] R-ACK empfangen → Message-Status wechselt von `sent` zu `routed`
- [ ] Channel-ACK empfangen → Message-Status wechselt zu `delivered`
- [ ] R-ACK mit `MAILBOX_FULL` → Warning im Log
- [ ] R-ACK mit `HANDLE_EXPIRED` → Message-Status `failed`
- [ ] Kein R-ACK nach 60s → Hops werden negativ gescored, Retry mit neuen Hops
- [ ] Hop-Selektion bevorzugt höher-gescorte Nodes
- [ ] Chat UI zeigt unterschiedliche Icons für jeden Status
- [ ] Channel-ACK wird automatisch gesendet wenn Bob eine Nachricht empfängt
- [ ] Node-Scores persistieren über App-Restarts (Drift)

## Open Questions

1. Soll Channel-ACK automatisch (bei Empfang) oder manuell (bei Lesen) gesendet werden?
2. Wie viel Gewicht sollen die Scores bei der Hop-Selektion haben vs. Zufall (für Diversität)?
3. Soll der User die Status-Icons sehen können, oder ist das zu technisch?
4. Wie mit R-ACK für RGB-Replies umgehen? Der OH-Node hat keinen Return-Path für RGB-basierte Zustellungen.
