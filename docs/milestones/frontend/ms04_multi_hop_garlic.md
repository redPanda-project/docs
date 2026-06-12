# Frontend MS04: Garlic Wrapping & Hop Selection

## Status: Missing

> **Backend-Abhängigkeit erfüllt**: [Backend MS04](../backend/ms04_multi_hop_garlic.md) ist Done (2026-06-12, redpandaj [#224](https://github.com/redPanda-project/redpandaj/pull/224)) —
> Relay-Peeling funktioniert, `PeerInfoProto.encryption_public_key` wird im Peer-Austausch mitgeliefert.
>
> **Verbindlich sind die [Decisions (Backend-MS04) in der Master-Spec](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms04_multi_hop_garlic.md#decisions-backend-ms04-2026-06-12)** —
> der Pseudo-Code unten weicht in Details ab und ist entsprechend zu korrigieren:
> HKDF-Info ist `"flaschenpost-v2"` (nicht `"garlic-v2"`), `oh_id` ist **20 Bytes** mit explizitem
> `payload_len` (4 B) im `CMD_DELIVER`-Plaintext, der Paket-Header ist 73 B (separates
> `ciphertext_len`-Feld, Tag gehört zum Ciphertext), Transport ist das neue Command
> `FLASCHENPOST_V2 = 142` (`[cmd][len:4][2048-B-Paket]`), und FORWARD-Plaintexte enthalten den
> Body der nächsten Schicht **ohne** eigenes Padding (das Relay füllt beim Rebuild neu auf).

## Goal

Mobile Client baut 3-Layer Garlic-Pakete (Flaschenpost v2). Wählt 3 Relay-Hops aus bekannten Peers. Jedes Paket ist exakt 2048 Bytes. Nachrichten werden nicht mehr direkt an den OH-Node gesendet, sondern über den Garlic-Pfad geroutet.

## Prerequisites

- Frontend MS03 Done — X25519, AES-256-GCM, HKDF verfügbar
- Backend MS04 Done — Relay-Peeling + Forwarding funktioniert

## Current State

| Component | File | Status |
|-----------|------|--------|
| Garlic wrapper | `garlic_message_wrapper.dart` | v2 Format nach Frontend MS03 — single layer |
| Peer management | `redpanda_light_client.dart` | Done — kennt Peers mit IP/Port/NodeId |
| Peer encryption keys | — | Missing — `PeerInfoProto.encryption_public_key` wird nicht gespeichert |
| Hop selection | — | Missing |

## Spec

### 1. Peer Encryption Key Speicherung

**`PeerInfoProto` Parsing erweitern:**

Beim Empfang von `SendPeerList` die `encryption_public_key` (32 bytes X25519) extrahieren und lokal speichern.

```dart
class PeerInfo {
  final String ip;
  final int port;
  final Uint8List nodeId;          // KademliaId, 20 bytes
  final Uint8List encryptionKey;   // NEW: X25519 public key, 32 bytes
}
```

**Drift `Peers` Table erweitern:**
- Neue Column `encryption_public_key TEXT` (hex-encoded, 64 chars).

### 2. Hop Selector

**Neue Datei `garlic/hop_selector.dart`:**

```dart
class HopSelector {
  final PeerRepository peerRepo;

  /// Wählt 3 Relay-Hops aus.
  /// Constraints:
  /// - Alle Hops müssen eine encryption_public_key haben
  /// - Kein Hop == eigener connected Node (Anti-Korrelation)
  /// - Kein Hop == Ziel-OH-Node
  /// - Möglichst unterschiedliche KademliaId-Präfixe (Diversität)
  List<PeerInfo> selectHops({
    required KademliaId destination,
    required KademliaId myNodeId,
    int count = 3,
  }) {
    final candidates = peerRepo.getKnownPeers()
      .where((p) => p.encryptionKey != null)
      .where((p) => p.nodeId != destination)
      .where((p) => p.nodeId != myNodeId)
      .toList();

    if (candidates.length < count) {
      throw InsufficientHopsException('Only ${candidates.length} candidates, need $count');
    }

    // Diversitäts-basierte Auswahl (unterschiedliche KademliaId-Präfixe)
    candidates.shuffle(SecureRandom());
    return candidates.take(count).toList();
  }
}
```

### 3. Garlic Builder (3-Layer)

**Neue Datei `garlic/garlic_builder.dart`:**

```dart
class GarlicBuilder {
  static const packetSize = 2048;
  static const headerSize = 85; // version(1) + packetId(4) + nextHop(20) + nonce(12) + ephPub(32) + ciphLen(4) + tag(16)

  /// Baut ein 3-Layer Garlic-Paket.
  /// Path: sender → H1 → H2 → H3 → destination
  Uint8List build({
    required List<PeerInfo> hops,     // [H1, H2, H3]
    required KademliaId destination,  // OH-Node KademliaId
    required Uint8List ohId,          // 32-byte OH ID
    required Uint8List payload,       // verschlüsselte Nachricht
  }) {
    // Layer 3 (innermost, für H3):
    final plaintext3 = BytesBuilder()
      ..addByte(0x02) // CMD_DELIVER
      ..add(ohId)     // 32 bytes
      ..add(payload)
      ..add(randomPadding(/*fill to max inner size*/));
    final layer3 = _encryptLayer(hops[2].encryptionKey, destination, plaintext3.toBytes());

    // Layer 2 (für H2):
    final plaintext2 = BytesBuilder()
      ..addByte(0x01) // CMD_FORWARD
      ..add(hops[2].nodeId) // next_hop = H3
      ..add(layer3)
      ..add(randomPadding(/*fill*/));
    final layer2 = _encryptLayer(hops[1].encryptionKey, hops[2].nodeId, plaintext2.toBytes());

    // Layer 1 (outermost, für H1):
    final plaintext1 = BytesBuilder()
      ..addByte(0x01) // CMD_FORWARD
      ..add(hops[1].nodeId) // next_hop = H2
      ..add(layer2)
      ..add(randomPadding(/*fill to packetSize*/));
    final packet = _encryptLayer(hops[0].encryptionKey, hops[1].nodeId, plaintext1.toBytes());

    assert(packet.length == packetSize);
    return packet;
  }

  Uint8List _encryptLayer(Uint8List targetEncPub, KademliaId nextHop, Uint8List plaintext) {
    final ephemeral = CryptoUtils.generateEncryptionKeypair();
    final shared = CryptoUtils.x25519(ephemeral.private, targetEncPub);
    final key = CryptoUtils.hkdf(shared, ephemeral.public, "garlic-v2", 32);
    final nonce = SecureRandom(12).bytes;
    final ciphertext = CryptoUtils.aesGcmEncrypt(key, nonce, plaintext, nextHop.bytes);

    return BytesBuilder()
      ..addByte(0x02)           // version
      ..addUint32(Random().nextInt(0xFFFFFFFF)) // packet_id
      ..add(nextHop.bytes)      // 20 bytes
      ..add(nonce)              // 12 bytes
      ..add(ephemeral.public)   // 32 bytes
      ..addUint32(ciphertext.length)
      ..add(ciphertext)
      ..add(paddingTo(packetSize))
      ..toBytes();
  }
}
```

### 4. sendMessage() umbauen

**`RedPandaLightClient.sendMessage()` — Garlic-Routing statt Direkt-Send:**

```dart
Future<String> sendMessage(String channelId, String content) async {
  final channel = await db.getChannel(channelId);
  final bobOH = channel.peerOhDescriptor!;

  // 1. Verschlüsseln mit K_enc
  final encryptedPayload = encryptChannelMessage(channel, utf8.encode(content));

  // 2. 3 Hops auswählen
  final hops = hopSelector.selectHops(
    destination: bobOH.nodeKademliaId,
    myNodeId: myKademliaId,
  );

  // 3. 3-Layer Garlic-Paket bauen
  final packet = GarlicBuilder().build(
    hops: hops,
    destination: bobOH.nodeKademliaId,
    ohId: bobOH.handleId,
    payload: encryptedPayload,
  );

  // 4. An H1 senden
  await sendToNode(hops[0], packet);

  return messageId;
}
```

### 5. Fallback bei zu wenigen Hops

Wenn weniger als 3 Peers mit `encryption_public_key` bekannt sind:

1. Zuerst: Peer-Liste von verbundenen Nodes anfordern.
2. Wenn immer noch <3: Warnung anzeigen, mit weniger Hops senden (reduzierte Privatsphäre).
3. Minimum: 1 Hop (direktes Routing an OH-Node, wie in MS01).

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `garlic/garlic_builder.dart` | 3-Layer Garlic-Pakete bauen |
| **New**: `garlic/hop_selector.dart` | 3 Relay-Hops auswählen |
| `client/redpanda_light_client.dart` | `sendMessage()` über Garlic statt direkt; Peer-Keys speichern |
| `garlic_message_wrapper.dart` | Entfällt / wird durch `garlic_builder.dart` ersetzt |
| `database.dart` | Migration v8: `Peers.encryption_public_key` Column |
| `peer_repository.dart` | `encryption_public_key` in `PeerInfo` Parsing |

## Acceptance Criteria

- [ ] Nachrichten werden über 3 Hops geroutet (nicht mehr direkt an OH-Node)
- [ ] Alle Flaschenpost v2 Pakete sind exakt 2048 Bytes
- [ ] Hop-Selektion vermeidet eigenen Node und Ziel-OH-Node
- [ ] X25519 Encryption Keys werden aus `PeerInfoProto` geparst und in Drift gespeichert
- [ ] Bei <3 verfügbaren Hops: Fallback auf weniger Hops mit Warnung
- [ ] End-to-End: Alice baut Garlic → 3 Relays peelen → Nachricht in Bob's OH-Mailbox

## Open Questions

1. Soll der Client auch Garlic-Pakete für `fetchMessages()` verwenden (anonymes Fetching)?
2. Wie mit Hop-Failure umgehen — komplettes Retry mit neuen Hops, oder nur den fehlenden Hop ersetzen?
3. Soll die Paketgröße konfigurierbar sein, oder fix 2048 Bytes?
4. Ab wie vielen bekannten Peers ist die Hop-Diversität „gut genug"?
