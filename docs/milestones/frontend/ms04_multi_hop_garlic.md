# Frontend MS04: Garlic Wrapping & Hop Selection

## Status: Done (2026-06-12 — mobile [#29](https://github.com/redPanda-project/redpanda-mobile/pull/29))

> **Backend-Abhängigkeit erfüllt**: [Backend MS04](../backend/ms04_multi_hop_garlic.md) ist Done (2026-06-12, redpandaj [#224](https://github.com/redPanda-project/redpandaj/pull/224)) —
> Relay-Peeling funktioniert, `PeerInfoProto.encryption_public_key` wird im Peer-Austausch mitgeliefert.
>
> **Verbindlich sind die [Decisions (Backend-MS04) in der Master-Spec](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms04_multi_hop_garlic.md#decisions-backend-ms04-2026-06-12)** —
> der Pseudo-Code unten weicht in Details ab; die Implementierung folgt den Decisions:
> HKDF-Info ist `"flaschenpost-v2"` (nicht `"garlic-v2"`), `oh_id` ist **20 Bytes** mit explizitem
> `payload_len` (4 B) im `CMD_DELIVER`-Plaintext, der Paket-Header ist 73 B (separates
> `ciphertext_len`-Feld, Tag gehört zum Ciphertext), Transport ist das neue Command
> `FLASCHENPOST_V2 = 142` (`[cmd][len:4][2048-B-Paket]`), und FORWARD-Plaintexte enthalten den
> Body der nächsten Schicht **ohne** eigenes Padding (das Relay füllt beim Rebuild neu auf).
> Die clientseitigen Festlegungen stehen in den
> [Decisions (Frontend-MS04) der Master-Spec](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms04_multi_hop_garlic.md#decisions-frontend-ms04-2026-06-12).

## Goal

Mobile Client baut 3-Layer Garlic-Pakete (Flaschenpost v2). Wählt 3 Relay-Hops aus bekannten Peers. Jedes Paket ist exakt 2048 Bytes. Nachrichten werden nicht mehr direkt an den OH-Node gesendet, sondern über den Garlic-Pfad geroutet.

## Prerequisites

- Frontend MS03 Done — X25519, AES-256-GCM, HKDF verfügbar
- Backend MS04 Done — Relay-Peeling + Forwarding funktioniert

## Current State

| Component | File | Status |
|-----------|------|--------|
| Garlic builder (3-Layer) | `garlic/garlic_builder.dart` | Done — Flaschenpost v2, fixe 2048 B, Format-Lock-Tests (ersetzt `garlic_message_wrapper.dart`) |
| Peer management | `redpanda_light_client.dart` | Done — kennt Peers mit IP/Port/NodeId |
| Peer encryption keys | `peer_stats.dart`, `database.dart` (Drift v11) | Done — aus `PeerInfoProto` Feld 4 geparst (Fallback: Bytes 32..63 des node_id-Exports), persistiert |
| Hop selection | `garlic/hop_selector.dart` | Done — Ausschluss Submit-Node + OH-Endpoint, Präfix-Diversität |

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
  static const headerSize = 73; // version(1) + packetId(4) + nextHop(20) + nonce(12) + ephPub(32) + ctLen(4); GCM-Tag (16) gehört zum Ciphertext

  /// Baut ein 3-Layer Garlic-Paket (Wire-Format: Decisions Backend-MS04).
  /// Path: sender → H1 → H2 → H3(= peelt CMD_DELIVER) → OH-Mailbox
  Uint8List build({
    required List<PeerInfo> hops,     // [H1, H2, H3]
    required Uint8List ohId,          // 20-byte OH-KademliaId
    required Uint8List payload,       // verschlüsselte Nachricht (Envelope v4)
  }) {
    // Innerste Schicht (für H3): CMD_DELIVER mit explizitem payload_len —
    // optionales Padding hinter dem Payload ist erlaubt.
    final deliver = BytesBuilder()
      ..addByte(0x02)               // CMD_DELIVER
      ..add(ohId)                   // 20 bytes
      ..addUint32(payload.length)   // payload_len
      ..add(payload);
    var body = _encryptLayer(hops[2].encryptionKey, hops[2].nodeId, deliver.toBytes());

    // FORWARD-Schichten (für H2, dann H1): enthalten den Body der nächsten
    // Schicht OHNE eigenes Padding — das Relay füllt beim Rebuild neu auf.
    for (final (hop, inner) in [(hops[1], hops[2]), (hops[0], hops[1])]) {
      final forward = BytesBuilder()
        ..addByte(0x01)             // CMD_FORWARD
        ..add(inner.nodeId)         // inner next_hop (20 bytes)
        ..add(body);
      body = _encryptLayer(hop.encryptionKey, hop.nodeId, forward.toBytes());
    }

    // Äußeres Paket: nur die äußerste Schicht wird zum 2048-B-Paket verpackt.
    final packet = BytesBuilder()
      ..addByte(0x02)               // version
      ..addUint32(secureRandomUint32()) // packet_id
      ..add(hops[0].nodeId)         // next_hop = H1 (20 bytes)
      ..add(body);
    packet.add(randomPadding(packetSize - packet.length)); // auf exakt 2048 auffüllen
    assert(packet.length == packetSize);
    return packet.toBytes();
  }

  /// Liefert den Layer-Body [nonce(12)][ephemeral_pub(32)][ctLen(4)][ciphertext inkl. Tag].
  Uint8List _encryptLayer(Uint8List hopEncPub, Uint8List hopNodeId, Uint8List plaintext) {
    final ephemeral = CryptoUtils.generateEncryptionKeypair();
    final shared = CryptoUtils.x25519(ephemeral.private, hopEncPub);
    final key = CryptoUtils.hkdf(shared, ephemeral.public, "flaschenpost-v2", 32);
    final nonce = SecureRandom(12).bytes;
    // AAD = KademliaId des Hops, der diese Schicht peelt
    final ciphertext = CryptoUtils.aesGcmEncrypt(key, nonce, plaintext, hopNodeId);

    return BytesBuilder()
      ..add(nonce)                  // 12 bytes
      ..add(ephemeral.public)       // 32 bytes
      ..addUint32(ciphertext.length) // inkl. 16-byte GCM-Tag
      ..add(ciphertext)
      ..toBytes();
  }
}
```

Versand an H1 über das Command `FLASCHENPOST_V2 = 142` (`[cmd][len:4][2048-B-Paket]`) an den
verbundenen Full Node. Größenbudget bei 3 Hops: max. 1764 B Deliver-Payload (Decision 6).

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
| `database.dart` | Migration v11 (Spec ging von v8 aus): `Peers.encryptionPublicKey` Column (SQL: `encryption_public_key`) |
| `peer_repository.dart` | `encryption_public_key` in `PeerInfo` Parsing |

## Acceptance Criteria

- [x] Nachrichten werden über 3 Hops geroutet (nicht mehr direkt an OH-Node) *(`sendMessage()` via `FLASCHENPOST_V2 = 142`; ScriptedSocket-Wire-Tests + E2E)*
- [x] Alle Flaschenpost v2 Pakete sind exakt 2048 Bytes *(Format-Lock-Test, `GarlicBuilder.packetSize`)*
- [x] Hop-Selektion vermeidet eigenen Node und Ziel-OH-Node *(Ausschluss nach Adresse **und** KademliaId; OH-Endpoint via `addChannelKeys(peerOhEndpoint:)`)*
- [x] X25519 Encryption Keys werden aus `PeerInfoProto` geparst und in Drift gespeichert *(Feld 4 + Fallback node_id-Export Bytes 32..63; Drift v11)*
- [x] Bei <3 verfügbaren Hops: Fallback auf weniger Hops mit Warnung *(0 Kandidaten → direkter MS02b-Deposit; Frontend-Decision 1)*
- [x] End-to-End: Alice baut Garlic → 3 Relays peelen → Nachricht in Bob's OH-Mailbox *(`ms04_multi_hop_garlic_test.dart`, 4 echte Nodes)*

## Open Questions

Beantwortet durch die [Decisions (Frontend-MS04) in der Master-Spec](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms04_multi_hop_garlic.md#decisions-frontend-ms04-2026-06-12):

1. ~~Soll der Client auch Garlic-Pakete für `fetchMessages()` verwenden (anonymes Fetching)?~~ → Nein, deferred auf MS05 (braucht Rückkanal = Reverse Garlic; Frontend-Decision 2).
2. ~~Wie mit Hop-Failure umgehen — komplettes Retry mit neuen Hops, oder nur den fehlenden Hop ersetzen?~~ → Komplettes Re-Send mit frisch gewählten Hops über die MS02-Retry-Queue (Frontend-Decision 3).
3. ~~Soll die Paketgröße konfigurierbar sein, oder fix 2048 Bytes?~~ → Fix 2048 B (Konstante, wie Backend; Frontend-Decision 4).
4. ~~Ab wie vielen bekannten Peers ist die Hop-Diversität „gut genug"?~~ → Kein Schwellwert; Best-Effort-Präfix-Diversität (Frontend-Decision 5).
