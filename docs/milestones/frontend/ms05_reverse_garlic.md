# Frontend MS05: RGB Builder & Session Tags

## Status: Missing

> **Backend-Abhängigkeit**: Blocked bis [Backend MS05](../backend/ms05_reverse_garlic.md) Done.
> Benötigt: `CMD_DELIVER` mit `session_tag` wird korrekt in der Mailbox gespeichert, `MailItem.session_tag` wird im `FetchResponse` mitgeliefert.

## Goal

Alice baut Reverse Garlic Blocks (RGBs) und hängt sie an ausgehende Nachrichten an. Bob nutzt das RGB, um Replies an Alice zu senden, ohne Alices OH-Node zu kennen. Alice korreliert eingehende Replies über Session-Tags mit dem richtigen Channel.

## Prerequisites

- Frontend MS04 Done — Garlic-Builder, Hop-Selektion
- Backend MS05 Done — `MailItem.session_tag` in Mailbox gespeichert und via Fetch zurückgegeben

## Current State

| Component | File | Status |
|-----------|------|--------|
| Garlic Builder | `garlic/garlic_builder.dart` (aus MS04) | Done — forward path only |
| Session Tags | — | Missing |
| RGB | — | Missing |

## Spec

### 1. RGB Builder

**Neue Datei `garlic/rgb_builder.dart`:**

Alice baut ein RGB (pre-encrypted return path) für Bob:

```dart
class RGBBuilder {
  final HopSelector hopSelector;

  /// Baut ein Reverse Garlic Block.
  /// Return path: Bob → H1' → H2' → H3' → Alice's OH
  ReverseGarlicBlock build({
    required OHRegistration aliceOH,   // Alice's eigenes OH
    required KademliaId aliceOHNode,   // KademliaId des OH-Nodes
  }) {
    // Session-Tag generieren
    final sessionTag = SecureRandom(16).bytes;

    // 3 Return-Hops wählen (andere als Forward-Hops!)
    final returnHops = hopSelector.selectHops(
      destination: aliceOHNode,
      myNodeId: myKademliaId,
    );

    // Layer 3 (innermost, für H3'):
    final plaintext3 = BytesBuilder()
      ..addByte(0x02) // CMD_DELIVER
      ..add(aliceOH.ohId)   // 32 bytes
      ..add(sessionTag)     // 16 bytes
      ..add(/*placeholder for reply payload*/)
      ..add(randomPadding());
    final layer3 = encryptLayer(returnHops[2].encryptionKey, aliceOHNode, plaintext3.toBytes());

    // Layer 2 (für H2'):
    final plaintext2 = BytesBuilder()
      ..addByte(0x01) // CMD_FORWARD
      ..add(returnHops[2].nodeId)
      ..add(layer3)
      ..add(randomPadding());
    final layer2 = encryptLayer(returnHops[1].encryptionKey, returnHops[2].nodeId, plaintext2.toBytes());

    // Layer 1 (outermost, für H1'):
    final plaintext1 = BytesBuilder()
      ..addByte(0x01) // CMD_FORWARD
      ..add(returnHops[1].nodeId)
      ..add(layer2)
      ..add(randomPadding());
    final layer1 = encryptLayer(returnHops[0].encryptionKey, returnHops[1].nodeId, plaintext1.toBytes());

    return ReverseGarlicBlock(
      version: 1,
      expiryTs: DateTime.now().add(Duration(hours: 24)).millisecondsSinceEpoch,
      sessionTag: sessionTag,
      firstHop: returnHops[0].nodeId,
      encryptedLayers: layer1,
    );
  }
}
```

### 2. RGB Data Model

**Neue Datei `domain/reverse_garlic_block.dart`:**

```dart
class ReverseGarlicBlock extends Equatable {
  final int version;                 // 1
  final int expiryTs;                // Unix ms
  final Uint8List sessionTag;        // 16 bytes
  final Uint8List firstHop;          // 20-byte KademliaId
  final Uint8List encryptedLayers;   // pre-encrypted onion

  Uint8List serialize();
  factory ReverseGarlicBlock.deserialize(Uint8List bytes);
}
```

### 3. RGB in Outgoing Messages

**`sendMessage()` — Erweiterung:**

```dart
Future<String> sendMessage(String channelId, String content) async {
  final channel = await db.getChannel(channelId);

  // RGB bauen
  final rgb = rgbBuilder.build(
    aliceOH: myOHRegistration,
    aliceOHNode: myOHNodeKademliaId,
  );

  // Session-Tag lokal speichern (für Korrelation)
  await db.insertSessionTag(rgb.sessionTag, channelId);

  // ChannelMessage mit RGB
  final channelMsg = ChannelMessage()
    ..messageId = generateMessageId()
    ..content = utf8.encode(content)
    ..replyPath = rgb.toProto()
    ..timestamp = DateTime.now().millisecondsSinceEpoch;

  // Verschlüsseln + Garlic-Routing (wie MS04)
  final encrypted = encryptChannelMessage(channel, channelMsg.writeToBuffer());
  final packet = garlicBuilder.build(hops: ..., payload: encrypted);
  await sendToNode(hops[0], packet);
}
```

### 4. RGB verwenden (Reply senden)

Wenn Bob auf Alices Nachricht antwortet:

```dart
Future<void> replyViaRGB(Channel channel, ReverseGarlicBlock rgb, String content) async {
  // 1. Reply verschlüsseln mit K_enc
  final channelMsg = ChannelMessage()
    ..messageId = generateMessageId()
    ..content = utf8.encode(content)
    ..timestamp = DateTime.now().millisecondsSinceEpoch;
  final encrypted = encryptChannelMessage(channel, channelMsg.writeToBuffer());

  // 2. Reply in das RGB einsetzen
  // Das RGB ist ein pre-encrypted Paket — Bob setzt seinen Reply als Payload ein
  final packet = insertPayloadIntoRGB(rgb, encrypted);

  // 3. An RGB.firstHop senden
  await sendToNode(rgb.firstHop, packet);
}
```

### 5. Session-Tag Store

**Neue Datei `garlic/session_tag_store.dart`:**

```dart
class SessionTagStore {
  final AppDatabase db;

  /// Speichert session_tag → channel_id Mapping
  Future<void> store(Uint8List sessionTag, String channelId) async {
    await db.into(db.sessionTags).insert(SessionTagsCompanion.insert(
      tag: sessionTag,
      channelId: channelId,
      createdAt: DateTime.now(),
    ));
  }

  /// Lookup: session_tag → channel_id
  Future<String?> lookup(Uint8List sessionTag) async {
    final result = await (db.select(db.sessionTags)
      ..where((t) => t.tag.equals(sessionTag)))
      .getSingleOrNull();
    return result?.channelId;
  }

  /// Session-Tag nach Verwendung löschen (single-use)
  Future<void> consume(Uint8List sessionTag) async {
    await (db.delete(db.sessionTags)
      ..where((t) => t.tag.equals(sessionTag)))
      .go();
  }

  /// Abgelaufene Tags bereinigen
  Future<void> cleanup() async {
    await (db.delete(db.sessionTags)
      ..where((t) => t.createdAt.isSmallerThanValue(
        DateTime.now().subtract(Duration(hours: 48)))))
      .go();
  }
}
```

### 6. Eingehende Messages mit Session-Tag verarbeiten

**`fetchMessages()` — Erweiterung:**

```dart
for (final item in response.items) {
  String? channelId;

  if (item.sessionTag.isNotEmpty) {
    // Reply via RGB — Session-Tag Lookup
    channelId = await sessionTagStore.lookup(item.sessionTag);
    if (channelId != null) {
      await sessionTagStore.consume(item.sessionTag); // single-use
    }
  } else {
    // Direktnachricht — Channel-Zuordnung über OH
    channelId = oh.channelId;
  }

  if (channelId != null) {
    final channel = await db.getChannel(channelId);
    final plaintext = decryptChannelMessage(channel, item.payload);
    // ChannelMessage parsen, content extrahieren, in DB speichern
  }
}
```

### 7. RGB Rotation

- Alice hängt an **jede** ausgehende Nachricht ein frisches RGB an.
- Jedes RGB ist single-use — nach Bob's Reply ist es verbraucht.
- Abgelaufene Session-Tags (>48h) werden periodisch bereinigt.

### 8. Database Migration

**Schema v9:**
- Neue Table `session_tags`: `tag BLOB PK`, `channel_id TEXT`, `created_at DATETIME`
- Neue Table `received_rgbs`: `id INTEGER PK`, `channel_id TEXT`, `rgb_bytes BLOB`, `expiry_ts INTEGER`, `used BOOLEAN`

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `garlic/rgb_builder.dart` | RGBs bauen (pre-encrypted return paths) |
| **New**: `domain/reverse_garlic_block.dart` | RGB Data Model + Serialization |
| **New**: `garlic/session_tag_store.dart` | session_tag → channel_id Mapping |
| `garlic/garlic_builder.dart` | RGB-Payload-Insertion Logik |
| `client/redpanda_light_client.dart` | RGB in `sendMessage()` einbauen, Session-Tag bei Fetch verarbeiten |
| `database.dart` | Migration v9: `session_tags` + `received_rgbs` Tables |
| `providers.dart` | `sessionTagStoreProvider`, `rgbBuilderProvider` |

## Acceptance Criteria

- [ ] Alice baut ein RGB und hängt es an ihre Nachricht an
- [ ] Bob empfängt das RGB und kann damit eine Reply senden
- [ ] Die Reply traversiert 3 Hops und landet in Alices OH-Mailbox
- [ ] Alice korreliert die Reply über den Session-Tag zum richtigen Channel
- [ ] Jedes RGB ist single-use — nach Verbrauch wird der Session-Tag gelöscht
- [ ] Abgelaufene RGBs (>24h) werden nicht akzeptiert
- [ ] Bob kennt zu keinem Zeitpunkt Alices OH-Node oder IP-Adresse
- [ ] Zwei-Wege-Konversation funktioniert: Alice→Bob (Garlic), Bob→Alice (RGB), Alice→Bob (neues Garlic mit neuem RGB)

## Open Questions

1. Wie wird der Reply-Payload in das pre-encrypted RGB eingesetzt? XOR-Slot oder Append + Re-Encrypt?
2. Soll Alice mehrere RGBs pro Nachricht senden (Batch), für den Fall, dass Bob mehrere Replies schnell hintereinander sendet?
3. Fallback wenn alle RGBs von Alice verbraucht/abgelaufen sind — kann Bob direkt an Alices OH senden (wenn aus Channel-Setup bekannt)?
4. Wie groß darf ein RGB maximal sein, damit es noch in ein 2048-Byte Garlic-Paket passt?
