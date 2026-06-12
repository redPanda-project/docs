# Frontend MS03: Dart Crypto Migration

## Status: Done (2026-06-12)

> **Umgesetzt in mobile [#23](https://github.com/redPanda-project/redpanda-mobile/pull/23)
> (Primitives + Key-Model) und [#24](https://github.com/redPanda-project/redpanda-mobile/pull/24)
> (Handshake v23 + framed GCM)**, aufbauend auf Backend MS03
> (redpandaj [#221](https://github.com/redPanda-project/redpandaj/pull/221)):
> `CryptoUtils` auf Basis des `cryptography`-Packages (Ed25519/X25519/HKDF-SHA256/AES-256-GCM,
> RFC-Testvektoren), Ed25519 OH-Auth mit Signing-Bytes v2 (`[0x02 | CMD | …]`,
> `oh_auth_public_key` = 32-byte Verify-Key), Channel-Key-Model v3 (K_auth = Ed25519-Keypair,
> Channel-ID = `SHA256(K_enc ‖ K_auth_pub)`, QR v3 **ohne** `k_auth_priv`), Garlic v2
> (Raw-Wire-Format wie Backend), Message-Envelope v3 (`[0x03][nonce][ct+tag]`, AAD = Channel-ID),
> Drift-Migration v9 (destruktiv), TCP-Handshake v23 mit `GcmFramedCodec`
> (Counter-Nonces empfänger-enforced, max. 32 KiB/Frame). `pointycastle` ist entfernt —
> keine CTR/brainpool-Referenzen mehr. Volle E2E-Suite grün gegen das MS03-Referenz-JAR.
> Frontend-Entscheidungen: siehe
> [Decisions (Frontend) in der Master-Spec](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms03_authenticated_encryption.md#decisions-frontend-2026-06-12).
>
> Das **Message-Format v2** war bereits zuvor shipped (mobile PR #14) und wurde durch das
> GCM-Envelope v3 abgelöst (Master-Spec Open Question 6).

## Goal

Dart-Client auf die gleichen Crypto-Primitives umstellen wie der Server: Ed25519 (Signing), X25519 (Key Exchange), AES-256-GCM (authenticated encryption). Channel-Model aktualisieren. TCP-Handshake auf v23 umstellen.

## Prerequisites

- Frontend MS02 Done — stabile Basis vor Breaking Changes
- Backend MS03 Done — Server akzeptiert v23 Handshake, Garlic v2, Ed25519 Signaturen

## Current State (nach MS03)

| Component | File | Status |
|-----------|------|--------|
| TCP Handshake | `network/active_peer.dart`, `security/gcm_framed_codec.dart` | v23 — 64-byte Ed25519/X25519-Export, ephemerer X25519-Austausch, framed AES-256-GCM |
| Crypto-Primitives | `crypto/crypto_utils.dart` | Ed25519/X25519/HKDF-SHA256/AES-256-GCM (`cryptography`-Package) |
| OH-Auth | `crypto/oh_keypair.dart` | Ed25519, Signing-Bytes v2 (`[0x02 | CMD | …]`), 64-byte Signaturen |
| Channel K_auth | `domain/channel.dart` | Ed25519-Keypair (v3), private Seed nur auf dem erzeugenden Gerät |
| Message-Envelope | `crypto/message_crypto_v3.dart` | v3: AES-256-GCM, AAD = Channel-ID |
| Garlic wrapping | `domain/garlic_message_wrapper.dart` | v2 Raw-Wire-Format (GCM + X25519 + HKDF, AAD = Ziel-KademliaId) |

## Spec

### 1. Crypto-Utility-Klasse

**Neue Datei `crypto/crypto_utils.dart`:**

```dart
class CryptoUtils {
  // Ed25519
  static Ed25519Keypair generateSigningKeypair();
  static Uint8List sign(Uint8List privateKey, Uint8List message); // → 64 bytes
  static bool verify(Uint8List publicKey, Uint8List message, Uint8List signature);

  // X25519
  static X25519Keypair generateEncryptionKeypair();
  static Uint8List x25519(Uint8List privateKey, Uint8List publicKey); // → 32-byte shared secret

  // HKDF
  static Uint8List hkdf(Uint8List ikm, Uint8List salt, String info, int length);

  // AES-256-GCM
  static Uint8List aesGcmEncrypt(Uint8List key, Uint8List nonce, Uint8List plaintext, Uint8List aad);
  static Uint8List aesGcmDecrypt(Uint8List key, Uint8List nonce, Uint8List ciphertext, Uint8List aad);
}
```

**Library-Wahl:**
- Option A: `pointycastle` (pure Dart, plattformunabhängig, langsamer)
- Option B: `cryptography` Package (nutzt Platform Crypto auf iOS/Android, schneller)
- Empfehlung: `cryptography` für Performance auf Mobile

### 2. TCP Handshake v23

**`RedPandaLightClient._performHandshake()` — Umbau:**

```dart
// Bisher (v22):
// 1. Send magic + version(22) + mode + KademliaId + port
// 2. Exchange 65-byte brainpool public keys
// 3. ECDH → AES-CTR stream

// Neu (v23):
// 1. Send magic + version(23) + mode + KademliaId + port   (30 bytes, wie bisher)
// 2. Exchange Ed25519 verify keys:  [32 bytes] each side
// 3. Exchange X25519 ephemeral keys: [32 bytes] each side
// 4. shared = X25519(my_ephemeral, their_ephemeral)
// 5. client_key = HKDF(shared, salt=min(verifyA,verifyB), info="tcp-client")
// 6. server_key = HKDF(shared, salt=max(verifyA,verifyB), info="tcp-server")
// 7. Framed AES-256-GCM (counter-based nonce)
```

**Frame I/O:**
```dart
Future<void> sendFrame(Uint8List plaintext) async {
  final nonce = _buildNonce(_sendCounter++); // uint96 counter
  final ciphertext = CryptoUtils.aesGcmEncrypt(_sendKey, nonce, plaintext, Uint8List(0));
  final frame = BytesBuilder()
    ..addUint32(ciphertext.length)
    ..add(nonce)
    ..add(ciphertext);
  socket.add(frame.toBytes());
}

Future<Uint8List> readFrame() async {
  final length = await readUint32();
  final nonce = await readBytes(12);
  final ciphertext = await readBytes(length - 12);
  return CryptoUtils.aesGcmDecrypt(_recvKey, nonce, ciphertext, Uint8List(0));
}
```

### 3. OH-Keypair auf Ed25519

**`crypto/oh_keypair.dart` — Umbau:**

```dart
class OHKeypair {
  final Uint8List privateKey;  // 32 bytes Ed25519
  final Uint8List publicKey;   // 32 bytes Ed25519

  factory OHKeypair.generate() {
    final kp = CryptoUtils.generateSigningKeypair();
    return OHKeypair(privateKey: kp.private, publicKey: kp.public);
  }

  Uint8List sign(Uint8List signingBytes) {
    return CryptoUtils.sign(privateKey, signingBytes); // → 64 bytes
  }
}
```

- Public Key: 32 bytes (statt 65 bytes brainpool).
- Signatur: 64 bytes fix (statt variable-length DER).

### 4. Channel K_auth auf Ed25519 Keypair

**`channel.dart` — Umbau:**

```dart
class Channel extends Equatable {
  final String label;
  final List<int> encryptionKey;     // 32 bytes, AES-256 (unchanged)
  final List<int> authPrivateKey;    // 32 bytes, Ed25519 private  (NEW)
  final List<int> authPublicKey;     // 32 bytes, Ed25519 public   (NEW)
  final OHDescriptor? ohDescriptor;

  String get id => sha256(encryptionKey + authPublicKey).hex;
}
```

**QR JSON v3** (nur `K_enc` + Public-Material — der Auth-Private-Key verlässt das
erzeugende Gerät nie, Master-Spec Sektion 10):
```json
{
  "l": "label",
  "k_enc": "hex...",
  "k_auth_pub": "hex...",
  "oh": { "ep": "host:port", "id": "hex...", "pk": "hex..." },
  "v": 3
}
```

### 5. Garlic Message Wrapping v2

**`garlic_message_wrapper.dart` — Umbau:**

```dart
Uint8List wrapGarlicV2(Uint8List targetEncPubKey, KademliaId destination, Uint8List payload) {
  final ephemeral = CryptoUtils.generateEncryptionKeypair();
  final shared = CryptoUtils.x25519(ephemeral.private, targetEncPubKey);
  final key = CryptoUtils.hkdf(shared, ephemeral.public, "garlic-v2", 32);
  final nonce = SecureRandom(12).bytes;
  final aad = destination.bytes;
  final ciphertext = CryptoUtils.aesGcmEncrypt(key, nonce, payload, aad);

  return BytesBuilder()
    ..addByte(0x02)  // version
    ..addUint32(totalLength)
    ..add(destination.bytes)
    ..add(nonce)
    ..add(ephemeral.public)
    ..addUint32(ciphertext.length)
    ..add(ciphertext)
    ..toBytes();
}
```

### 6. Channel Encryption auf AES-256-GCM

Nachrichten-Verschlüsselung im Channel:

```dart
// Bisher: AES-256-CTR (kein Auth-Tag)
// Neu: AES-256-GCM

Uint8List encryptChannelMessage(Channel channel, Uint8List plaintext) {
  final nonce = SecureRandom(12).bytes;
  final ciphertext = CryptoUtils.aesGcmEncrypt(
    channel.encryptionKey, nonce, plaintext,
    Uint8List.fromList(utf8.encode(channel.id)), // AAD = channel ID
  );
  return Uint8List.fromList([...nonce, ...ciphertext]);
}
```

### 7. Database Migration

**Schema v7:**
- `Channels` Table: `authenticationKey` → `authPrivateKey` + `authPublicKey` (Ed25519 Keypair).
- Bestehende Channels mit shared-secret K_auth müssen migriert werden (oder destructive recreation für Dev).

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `crypto/crypto_utils.dart` | Ed25519, X25519, HKDF, AES-256-GCM Wrapper |
| `client/redpanda_light_client.dart` | Handshake v23: 32-byte Keys, framed GCM |
| `crypto/oh_keypair.dart` | brainpoolp256r1 → Ed25519 |
| `domain/channel.dart` | K_auth → Ed25519 Keypair, QR JSON v3, Channel ID update |
| `garlic_message_wrapper.dart` | v1 → v2: AES-256-GCM + X25519 + HKDF |
| `database.dart` | Migration v7: Channel-Schema für Ed25519 K_auth |
| `pubspec.yaml` | `cryptography` oder `pointycastle` Package hinzufügen/updaten |

## Acceptance Criteria

- [x] TCP-Verbindung nutzt Handshake v23 (32-byte Keys, framed AES-256-GCM)
- [x] Ein geflipptes Bit in einem TCP-Frame → Decryption-Fehler (kein stilles Corrumption) — Unit-Test: manipulierter/replayed Frame → Disconnect
- [x] OH-Registration nutzt Ed25519 Signaturen (64 bytes)
- [x] Channel K_auth ist ein Ed25519 Keypair (nicht mehr shared secret)
- [x] Garlic-Messages nutzen v2 Format (AES-256-GCM + X25519)
- [x] Channel-Verschlüsselung nutzt AES-256-GCM mit Channel-ID als AAD (Envelope v3, `0x03`)
- [x] QR-Code nutzt v3 Format — ohne `k_auth_priv`
- [x] Alle bestehenden Tests passen (oder sind für neues Crypto angepasst)

## Open Questions

Beantwortet durch die [Decisions (Frontend) in der Master-Spec](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms03_authenticated_encryption.md#decisions-frontend-2026-06-12):

1. ~~`pointycastle` vs `cryptography` Package?~~ → `cryptography` (pointycastle 4.x hat kein Ed25519/X25519); `pointycastle` komplett entfernt.
2. ~~Sollen alte QR-Codes (v1, v2) noch lesbar sein?~~ → Nein, klare Fehlermeldung; beide Seiten erzeugen den Channel mit v3-QR neu.
3. ~~Wie Channels migrieren, die bereits mit v1/v2 erstellt wurden?~~ → Destruktive Drift-Migration v9 (Channels/Messages/OH-Handles), Breaking Change im Testnetz.
