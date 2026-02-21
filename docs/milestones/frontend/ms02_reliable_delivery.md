# Frontend MS02: Retry, Dedup & Polling

## Status: Missing

> **Backend-Abhängigkeit**: Blocked bis [Backend MS02](../backend/ms02_reliable_delivery.md) Done.
> Benötigt: Sequence-basierte Mailbox, `AckFetchRequest` Command, `mailbox_overflow` Flag.

## Goal

Zuverlässige Nachrichtenzustellung auf Client-Seite: Retry-Logik für fehlgeschlagene Sends, Dedup für doppelt empfangene Nachrichten, AckFetch nach erfolgreichem Persist, OH Auto-Renewal.

## Prerequisites

- Frontend MS01 Done — sendMessage() + fetchMessages() funktionieren
- Backend MS02 Done — sequence-basierte Mailbox + AckFetch Command

## Current State

| Component | File | Status |
|-----------|------|--------|
| Message Sending | `redpanda_light_client.dart` | Done nach Frontend MS01 — kein Retry |
| Message Fetching | `redpanda_light_client.dart` | Done nach Frontend MS01 — kein AckFetch |
| Message Status | `database.dart` → `Messages.status` | Done — integer enum |
| Dedup | — | Missing |
| OH Expiry Tracking | — | Missing |

## Spec

### 1. Retry-Logik für Sends

**`SendRetryQueue` (neue Klasse):**

```dart
class SendRetryQueue {
  final AppDatabase db;
  final RedPandaLightClient client;
  Timer? _retryTimer;

  void start() {
    _retryTimer = Timer.periodic(Duration(seconds: 60), (_) => _retryPending());
  }

  Future<void> _retryPending() async {
    final pending = await db.getPendingMessages(); // status == 0
    for (final msg in pending) {
      if (msg.retryCount >= 10) {
        await db.updateMessageStatus(msg.id, MessageStatus.failed); // status 5
        continue;
      }
      try {
        await client.sendMessage(msg.channelId, msg.content);
        await db.updateMessageStatus(msg.id, MessageStatus.sent); // status 1
      } catch (e) {
        await db.incrementRetryCount(msg.id);
        // Exponential backoff: nächster Retry in 2^retryCount Minuten (max 30 min)
      }
    }
  }
}
```

### 2. AckFetch nach erfolgreichem Persist

**`RedPandaLightClient.fetchMessages()` — Erweiterung:**

```dart
Future<List<DecryptedMessage>> fetchMessages(OHRegistration oh) async {
  // ... bestehender Fetch-Code ...

  // Nachrichten in Drift speichern
  for (final msg in decryptedMessages) {
    await db.insertMessage(...);
  }

  // AckFetch senden — Server löscht bestätigte Items
  if (response.items.isNotEmpty) {
    final highestSeq = response.items.last.sequenceId;
    final ackRequest = AckFetchRequest()
      ..ohId = oh.ohId
      ..ackedSequenceId = highestSeq
      ..timestampMs = DateTime.now().millisecondsSinceEpoch
      ..nonce = SecureRandom(16).bytes
      ..signature = oh.keypair.sign(buildSigningBytes(CMD_ACK_FETCH, ...));

    await sendCommand(CMD_ACK_FETCH, ackRequest.writeToBuffer());
  }

  return decryptedMessages;
}
```

### 3. Message Dedup

**Vor dem Insert in Drift:**

```dart
Future<bool> insertMessageIfNew(DecryptedMessage msg) async {
  final exists = await db.messageExistsByMessageId(msg.messageId);
  if (exists) return false; // Doppelt — ignorieren
  await db.insertMessage(...);
  return true;
}
```

- Drift-Schema: `message_id` Column (TEXT, UNIQUE) in `Messages` Table.
- Handles den Fall, dass AckFetch fehlschlägt aber die Nachricht schon lokal gespeichert ist.

### 4. OH Auto-Renewal

**Periodischer Check (in Background-Polling-Timer):**

```dart
Timer.periodic(Duration(minutes: 5), (_) async {
  for (final oh in registeredOHs) {
    if (oh.expiresAt - DateTime.now().millisecondsSinceEpoch < Duration(days: 1).inMilliseconds) {
      try {
        await registerOutboundHandle(existingOhId: oh.ohId); // Re-Register
      } catch (e) {
        // Retry next cycle
      }
    }
  }
});
```

### 5. Message Status UI

**`chat_screen.dart` — Status-Icons:**

| Status | Icon | Bedeutung |
|--------|------|-----------|
| 0 (pending) | Clock | Wird gesendet |
| 1 (sent) | Single checkmark | An Netzwerk übergeben |
| 5 (failed) | Red X | Fehlgeschlagen nach 10 Retries |

(Weitere Status wie `routed`, `delivered` kommen in Frontend MS06.)

### 6. Mailbox Overflow Warning

Wenn `FetchResponse.mailbox_overflow == true`:

```dart
if (response.mailboxOverflow) {
  // Snackbar oder Banner: "Einige ältere Nachrichten könnten verloren sein."
  log.warning('Mailbox overflow detected for OH ${oh.ohId}');
}
```

### 7. Database Migration

**Schema v6:**
- `Messages` Table: Neue Column `message_id TEXT UNIQUE` (für Dedup).
- `Messages` Table: Neue Column `retry_count INTEGER DEFAULT 0`.
- `OutboundHandles` Table (aus Frontend MS01): Neue Column `last_cursor INTEGER DEFAULT 0`.

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `send_retry_queue.dart` | Retry-Logik mit exponential backoff |
| `redpanda_light_client.dart` | `ackFetch()` implementieren, Auto-Renewal Timer |
| `database.dart` | Migration v6: `message_id` UNIQUE, `retry_count`, `last_cursor` |
| `chat_screen.dart` | Message Status Icons (pending/sent/failed), Overflow-Warning |
| `providers.dart` | `sendRetryQueueProvider`, `pendingMessageCountProvider` |
| `domain/channel.dart` | — keine Änderung |

## Acceptance Criteria

- [ ] Fehlgeschlagene Sends werden automatisch retried (max 10×, exponential backoff)
- [ ] Nach 10 fehlgeschlagenen Retries: Status → `failed`, UI zeigt rotes X
- [ ] `AckFetchRequest` wird nach erfolgreichem Persist gesendet
- [ ] Doppelte Nachrichten (gleiche `message_id`) werden nicht in Drift eingefügt
- [ ] OH wird automatisch erneuert wenn `expires_at - now < 1 Tag`
- [ ] Chat UI zeigt Status-Icons (Clock=pending, Checkmark=sent, X=failed)
- [ ] Mailbox-Overflow wird dem User als Warning angezeigt
- [ ] `last_cursor` wird persistent gespeichert — nach App-Restart kein erneutes Fetchen alter Nachrichten

## Open Questions

1. Soll der Retry-Timer auch im Background (App minimiert) laufen?
2. Soll `AckFetch` Failure den nächsten Fetch blockieren, oder unabhängig retry?
3. Braucht der User eine "Resend"-Taste für fehlgeschlagene Nachrichten?
