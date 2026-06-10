# Frontend MS01: OH Client & Chat Integration

## Status: Done

> **Backend-Abhängigkeit**: Blocked bis [Backend MS01](../backend/ms01_first_real_message.md) Done.
> Benötigt: Wire-Protokoll (Command Bytes + Protobuf), FlaschenpostPut → OH-Mailbox Routing funktioniert.

## Goal

`sendMessage()` implementieren (aktuell `UnimplementedError`). OH-Registration, Message-Sending via OH, Message-Fetching. Mock-Reply aus dem Chat-Screen entfernen. Echter End-to-End-Flow: Alice sendet → Bob empfängt.

## Prerequisites

- Backend MS01 Done — OH-Service stabil, FlaschenpostPut → Mailbox funktioniert
- TCP-Verbindung zu mindestens einem Full Node (bereits implementiert)

## Current State

| Component | File | Status |
|-----------|------|--------|
| TCP + Handshake | `redpanda_light_client.dart` | Done |
| `sendMessage()` | `client/redpanda_light_client.dart` | `throw UnimplementedError()` |
| Isolate-Wrapper | `client/isolate_client.dart` | Forwards `CmdSendMessage` → returns `"Queued"` (dummy) |
| Channel model | `domain/channel.dart` | Done — K_enc + K_auth, QR JSON v1 |
| Chat screen | `chat_screen.dart` | Mock auto-reply |
| Database | `database.dart` | Done — schema v5 |
| Providers | `providers.dart` | Done |
| Mock client | `mock/mock_redpanda_client.dart` | Fake message ID, 500ms delay |

## Spec

### 1. OHDescriptor Model

**Neue Datei `domain/oh_descriptor.dart`:**

```dart
class OHDescriptor extends Equatable {
  final String serverEndpoint;     // host:port des OH-Nodes
  final List<int> handleId;        // 32 bytes
  final List<int> authPublicKey;   // 65 bytes (brainpoolp256r1, wird in MS03 auf 32 bytes Ed25519)

  String toJson();                 // für QR-Code
  factory OHDescriptor.fromJson(String json);
}
```

### 2. OH-Keypair Generation (Dart)

**Neue Datei `crypto/oh_keypair.dart`:**

- ECDSA Keypair generieren (brainpoolp256r1 via PointyCastle, gleiche Kurve wie Backend in MS01).
- Public Key exportieren (65 bytes).
- Signing: `SHA256withECDSA(signingBytes)`.

### 3. OH Registration

**`RedPandaLightClient.registerOutboundHandle()`:**

```dart
Future<OHRegistration> registerOutboundHandle() async {
  final keypair = OHKeypair.generate();
  final ohId = SecureRandom(32).bytes;

  final request = RegisterOhRequest()
    ..ohId = ohId
    ..ohAuthPublicKey = keypair.publicKeyBytes
    ..requestedExpiresAt = DateTime.now().add(Duration(days: 7)).millisecondsSinceEpoch
    ..timestampMs = DateTime.now().millisecondsSinceEpoch
    ..nonce = SecureRandom(16).bytes
    ..signature = keypair.sign(buildSigningBytes(CMD_REGISTER_OH, ohId, ...));

  // Send: [CMD_REGISTER_OH][4 length][protobuf]
  await sendCommand(CMD_REGISTER_OH, request.writeToBuffer());
  final response = await readResponse(CMD_REGISTER_OH);

  if (response.status != Status.OK) throw OHRegistrationException(response.status);

  return OHRegistration(ohId: ohId, keypair: keypair, expiresAt: response.expiresAtMs);
}
```

### 4. Channel QR-Code v2

**`channel.dart` — Erweiterung:**

```dart
class Channel {
  final String label;
  final List<int> encryptionKey;      // 32 bytes
  final List<int> authenticationKey;  // 32 bytes
  final OHDescriptor? ohDescriptor;   // NEW: eigenes OH für eingehende Nachrichten
}
```

**QR JSON v2:**
```json
{
  "l": "label",
  "k_enc": "hex...",
  "k_auth": "hex...",
  "oh": { "ep": "host:port", "id": "hex...", "pk": "hex..." },
  "v": 2
}
```

Beim Scannen von Bob's QR speichert Alice Bob's `OHDescriptor` — dorthin sendet sie Nachrichten.

### 5. sendMessage() Implementation

**`RedPandaLightClient.sendMessage()`:**

```dart
Future<String> sendMessage(String channelId, String content) async {
  // 1. Channel + Bob's OHDescriptor laden
  final channel = await db.getChannel(channelId);
  final bobOH = channel.peerOhDescriptor;

  // 2. Verschlüsseln mit K_enc (AES-256)
  final iv = SecureRandom(16).bytes;
  final ciphertext = aesEncrypt(channel.encryptionKey, iv, utf8.encode(content));

  // 3. Payload bauen
  final payload = [...iv, ...ciphertext];

  // 4. FlaschenpostPut an Bob's OH-Node senden
  final flaschenpost = FlaschenpostPut()..content = payload;
  await sendCommand(CMD_FLASCHENPOST_PUT, flaschenpost.writeToBuffer());

  // 5. Lokale DB updaten
  final messageId = uuid.v4();
  await db.insertMessage(channel.uuid, 'me', content, status: MessageStatus.sent);

  return messageId;
}
```

### 6. fetchMessages() Implementation

**`RedPandaLightClient.fetchMessages()`:**

```dart
Future<List<DecryptedMessage>> fetchMessages(OHRegistration oh) async {
  final request = FetchRequest()
    ..ohId = oh.ohId
    ..limit = 50
    ..cursor = oh.lastCursor
    ..timestampMs = DateTime.now().millisecondsSinceEpoch
    ..nonce = SecureRandom(16).bytes
    ..signature = oh.keypair.sign(buildSigningBytes(CMD_FETCH, ...));

  await sendCommand(CMD_FETCH, request.writeToBuffer());
  final response = await readResponse(CMD_FETCH);

  if (response.status != Status.OK) throw FetchException(response.status);

  final messages = <DecryptedMessage>[];
  for (final item in response.items) {
    final iv = item.payload.sublist(0, 16);
    final ciphertext = item.payload.sublist(16);
    final plaintext = aesDecrypt(channel.encryptionKey, iv, ciphertext);
    messages.add(DecryptedMessage(
      id: item.messageId,
      content: utf8.decode(plaintext),
      receivedAt: item.receivedAtMs,
    ));
  }

  oh.lastCursor = response.nextCursor;
  return messages;
}
```

### 7. Mock-Reply entfernen

**`chat_screen.dart`:** Block zwischen `// START: Simulate receiving a reply (Mock logic)` und `// END: Mock logic` löschen.

### 8. Chat Screen → echtes sendMessage()

**`chat_screen.dart._sendMessage()`:**

```dart
void _sendMessage(String content) async {
  // Lokale DB
  await db.into(db.messages).insert(MessagesCompanion.insert(
    conversationId: widget.peerUuid,
    senderId: 'me',
    content: content,
    timestamp: DateTime.now(),
    status: Value(0), // pending
    type: Value(0),
  ));

  // Netzwerk
  try {
    await ref.read(redPandaClientProvider).sendMessage(widget.peerUuid, content);
    // Status → sent (1)
  } catch (e) {
    // Status → failed (5), Snackbar
  }
}
```

### 9. Background Polling

**Periodischer Timer in `RedPandaLightClient`:**

```dart
Timer.periodic(Duration(seconds: 30), (_) async {
  for (final oh in registeredOHs) {
    final messages = await fetchMessages(oh);
    for (final msg in messages) {
      await db.insertMessage(oh.channelId, 'peer', msg.content);
      _incomingMessageController.add(msg); // Stream
    }
  }
});
```

### 10. Incoming Message Provider

**`providers.dart`:**

```dart
final incomingMessagesProvider = StreamProvider<DecryptedMessage>((ref) {
  return ref.watch(redPandaClientProvider).incomingMessages;
});
```

## Protobuf Changes (Mobile-Side)

Keine Änderungen — die Mobile-Kopie von `outbound.proto` und `commands.proto` hat bereits alle nötigen Messages.

Optional: `ChannelMessage` Wrapper (siehe Open Questions).

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `domain/oh_descriptor.dart` | OHDescriptor Model |
| **New**: `crypto/oh_keypair.dart` | ECDSA Keypair Generation + Signing |
| `client/redpanda_light_client.dart` | `sendMessage()`, `registerOutboundHandle()`, `fetchMessages()` implementieren |
| `client/isolate_client.dart` | Command-Forwarding für OH-Operationen updaten |
| `domain/channel.dart` | `OHDescriptor` Feld, QR JSON v2 |
| `database.dart` | `OutboundHandles` Table: `oh_id`, `keypair_bytes`, `server_endpoint`, `expires_at`, `channel_id` |
| `chat_screen.dart` | Mock-Reply entfernen, echtes `sendMessage()` einbauen |
| `providers.dart` | `incomingMessagesProvider` Stream |

## Acceptance Criteria

- [ ] `sendMessage()` wirft kein `UnimplementedError` mehr
- [ ] OH wird auf einem Full Node registriert; `RegisterOhResponse.status == OK`
- [ ] OHDescriptor wird im QR-Code (v2) geteilt
- [ ] Nachricht an Bob's OH → Bob fetcht → Plaintext stimmt überein
- [ ] Chat UI zeigt echte Nachrichten, kein Mock-Reply
- [ ] Messages persistieren über App-Restart (Drift DB)
- [ ] Background Polling holt neue Nachrichten alle 30 Sekunden
- [ ] `incomingMessagesProvider` Stream triggert UI-Update bei neuen Nachrichten

## Open Questions

1. AES-Modus: AES-256-CTR (wie aktuelles Garlic) oder AES-256-GCM? → Wird in MS03 vereinheitlicht.
2. Soll `ChannelMessage` Protobuf eingeführt werden, oder reichen rohe Bytes in `MailItem.payload`?
3. OH-Registration: Bei App-Start oder erst bei Channel-Erstellung?
4. Wie Alice an Bob's OH-Node routen, wenn sie nicht direkt verbunden ist?
