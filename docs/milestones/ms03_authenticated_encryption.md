# MS03: Authenticated Encryption

## Status: Done (backend 2026-06-12, frontend 2026-06-12)

> **Backend umgesetzt in redpandaj [#221](https://github.com/redPanda-project/redpandaj/pull/221)** (2026-06-12):
> Ed25519/X25519-NodeId, GarlicMessage v2 (AES-256-GCM), TCP-Handshake v23 mit framed GCM,
> Ed25519 OH-Auth mit Signing-Versions-Byte, Dual-Version-Support v22/v23 — Entscheidungen
> im Abschnitt [Decisions (Backend)](#decisions-backend-2026-06-12).
>
> **Frontend umgesetzt in mobile [#23](https://github.com/redPanda-project/redpanda-mobile/pull/23) und
> [#24](https://github.com/redPanda-project/redpanda-mobile/pull/24)** (2026-06-12):
> `CryptoUtils` (Package `cryptography`: Ed25519/X25519/HKDF/AES-256-GCM), Handshake v23 mit
> framed GCM, Ed25519 OH-Auth mit Signing-Bytes v2, Channel-Key-Model v3 (QR ohne
> `k_auth_priv`), Garlic v2, Message-Envelope v3 — Entscheidungen im Abschnitt
> [Decisions (Frontend)](#decisions-frontend-2026-06-12). Details:
> [Frontend MS03](https://github.com/redPanda-project/docs/blob/main/docs/milestones/frontend/ms03_authenticated_encryption.md).

The codebase used brainpoolp256r1 (ECDH/ECDSA) with AES-CTR (no authentication). The ARC42 spec targets Ed25519 (signatures), X25519 (key exchange), and AES-256-GCM (authenticated encryption) — the backend now implements exactly that.

The **channel message format v2** (section 8 below — versioned `[0x02][IV][ciphertext][HMAC]` envelope, HKDF key separation, inner `ChannelMessage` protobuf, receiver-side dedup) shipped in the frontend (mobile PR #14). The backend primitive migration (Ed25519/X25519/AES-GCM) and the signing version byte shipped in redpandaj #221. The frontend primitive migration shipped in mobile #23/#24 — MS03 is complete. (The **[frontend ships]**/**[remains]** tags below are historical markers from the staged rollout.)

## Goal

Replace the current encryption stack with modern, authenticated primitives. Every encrypted payload must be authenticated (AEAD). Separate key types for signing (Ed25519) and encryption (X25519). Remove all legacy/insecure cipher references.

## Prerequisites

- MS01 (First Real Message) — so we have a working baseline to migrate
- MS02 (Reliable Delivery) — so message retransmission works during migration

## Current State

| What | Where | Status |
|------|-------|--------|
| Node identity keypair | `NodeId.java` — brainpoolp256r1, ECDH key used for both signing and encryption | Done — wrong curve |
| Garlic encryption | `GarlicMessage.java` — AES/CTR/NoPadding + ECDH ephemeral key | Done — no authentication tag |
| TCP stream cipher | `ConnectionHandler.java` — AES-CTR derived from ECDH shared secret | Done — no authentication |
| OH auth signatures | `OutboundAuth.java` — SHA256withECDSA | Done — wrong curve |
| Mobile channel keys | `channel.dart` — 256-bit K_enc + K_auth (shared secrets) | Done — K_auth should be a keypair |
| HashCash PoW | `NodeId.java` constructor — SHA256(SHA256(pubkey))[0] == 0 | Done — needs update for Ed25519 |
| RC4 references | Unknown — mentioned in ARC42 risks | To be found and removed |

## Spec

### 1. Key Type Separation

**Backend (`NodeId.java`):**

Currently, `NodeId` uses a single brainpoolp256r1 keypair for everything. Split into:

- **Signing key**: Ed25519 (for identity, authentication, ECDSA replacement)
  - Used for: OH auth signatures, message signing, node identity
  - Library: BouncyCastle `Ed25519PrivateKeyParameters` / `Ed25519PublicKeyParameters`
- **Encryption key**: X25519 (for ECDH key exchange)
  - Used for: Garlic message encryption, TCP stream key derivation
  - Library: BouncyCastle `X25519Agreement`

**`NodeId` new structure:**
```java
class NodeId {
    Ed25519PrivateKeyParameters signingKey;
    Ed25519PublicKeyParameters  verifyKey;    // 32 bytes
    X25519PrivateKeyParameters  encryptionKey;
    X25519PublicKeyParameters   encryptionPublicKey; // 32 bytes
    KademliaId kademliaId; // SHA-256(verifyKey)[0..20]
}
```

- Export format: `[32 signing priv][32 verify pub][32 enc priv][32 enc pub]` = 128 bytes
- Public export: `[32 verify pub][32 enc pub]` = 64 bytes (vs current 65 bytes for brainpool)

### 2. AES-256-GCM for Garlic Messages

**`GarlicMessage.java`:**

Replace AES-CTR with AES-256-GCM:

```
Encryption:
  1. ephemeral_x25519_keypair = generate()
  2. shared_secret = X25519(ephemeral_private, target_enc_public)
  3. derived_key = HKDF-SHA256(shared_secret, salt=ephemeral_public, info="garlic-v2")
  4. nonce = 12 random bytes (GCM standard)
  5. ciphertext || tag = AES-256-GCM(derived_key, nonce, plaintext, aad=destination_kademlia_id)

Wire format:
  [1 GMType][4 totalLen][20 destination][12 nonce][32 ephemeral_enc_pub]
  [4 ciphertextLen][N ciphertext + 16-byte GCM tag]
```

- The GCM authentication tag replaces the separate ECDSA signature (simpler, faster).
- AAD (additional authenticated data) = destination KademliaId — binds the ciphertext to its intended recipient.

### 3. AES-256-GCM for TCP Stream

**`ConnectionHandler.java`:**

Replace AES-CTR stream cipher with a framed AES-256-GCM protocol:

```
Handshake:
  1. Exchange Ed25519 verify keys (32 bytes each, vs current 65)
  2. Exchange X25519 ephemeral public keys (32 bytes each)
  3. shared_secret = X25519(my_ephemeral_priv, their_ephemeral_pub)
  4. client_key, server_key = HKDF-SHA256(shared_secret, salt=sorted_verify_keys, info="tcp-v2")

Frame format (each direction):
  [4 length][12 nonce][ciphertext + 16 GCM tag]
  nonce = counter (incremented per frame, never reused)
```

### 4. Channel Key Model Update

**`channel.dart`:**

Change `K_auth` from a shared 32-byte secret to an Ed25519 keypair:

```dart
class Channel {
  final String label;
  final List<int> encryptionKey;    // 32 bytes, AES-256 (unchanged)
  final List<int> authPrivateKey;   // 32 bytes, Ed25519 private key
  final List<int> authPublicKey;    // 32 bytes, Ed25519 public key
}
```

- `K_auth` is used to sign OHDescriptors and channel metadata.
- Channel ID = `SHA256(encryptionKey || authPublicKey)`.
- QR code JSON: `{"l":..., "k_enc":..., "k_auth_pub":..., "v":3}` — **public keys + `K_enc` only**. The earlier `k_auth_priv` field must be removed (see section 10); only public material belongs in the QR.

### 5. OH Auth Migration

**`OutboundAuth.java`:**

Replace SHA256withECDSA (brainpoolp256r1) with Ed25519:

- Signing bytes format stays the same.
- Signature = Ed25519(signing_bytes) — 64 bytes (vs variable-length DER ECDSA).
- Public key = 32 bytes (vs 65 bytes).

**`outbound.proto`:**
- `oh_auth_public_key` shrinks from 65 to 32 bytes — no proto schema change needed (it's `bytes`).

### 6. Remove RC4 and Legacy Ciphers

- Search entire codebase for `RC4`, `ARCFOUR`, `AES/CTR`, `brainpool`.
- Remove or replace all occurrences.
- Ensure no fallback paths re-introduce legacy ciphers.

### 7. Channel Message Format v2 — [frontend ships]

This is the format being implemented in the frontend right now. **Implement exactly as specified here; do not deviate.** It is independent of the primitive migration (sections 1–6) and ships first.

**Payload envelope** (the bytes carried inside the `FlaschenpostPut` / mailbox `MailItem.payload`):

```
[version 0x02][IV 16 bytes][ciphertext][HMAC-SHA256 32 bytes]
```

**Key separation (HKDF-SHA256 over the channel key `K_enc`):**

```
K_cipher = HKDF-SHA256(K_enc, salt = empty, info = "redpanda-msg-v2-cipher")
K_mac    = HKDF-SHA256(K_enc, salt = empty, info = "redpanda-msg-v2-mac")
```

- `K_cipher` is used for AES (current stack: AES-256-CTR; becomes AES-256-GCM after section 2).
- `K_mac` is used for the HMAC. This replaces the current `HMAC-key == encryption-key` anti-pattern.

**MAC:**

- HMAC-SHA256 computed over `[version || IV || ciphertext]` (i.e. the whole envelope except the trailing tag).
- Verification is **constant-time** (`constantTimeEquals`, no byte-wise early exit).

**Inner plaintext** = protobuf `ChannelMessage`:

```protobuf
message ChannelMessage {
  bytes message_id = 1;   // 16 bytes, sender-generated, REUSED across retries
  int64 timestamp_ms = 2;
  string content = 3;
}
```

- `message_id` is generated **once** by the sender and reused on every retry of the same logical message, so resends are de-duplicated rather than appearing as new messages.
- **Dedup at the receiver** is per `(channel, message_id)` — not global. This fixes the current global-`messageId` dedup bug and the empty-`message_id` contract gap (the server-side `MailItem.message_id` is no longer the dedup key; the sender-generated id inside the authenticated plaintext is).

> Why now: this format gives key separation, constant-time MAC, sender-side dedup, and a per-message replay/ordering anchor (`message_id` + `timestamp_ms`) **before** the heavier Ed25519/X25519/GCM migration — and the `0x02` version byte lets the GCM migration (section 2) reuse the same envelope slot later.

### 8. Signing Version/Algorithm Byte (all signing-byte formats) — [remains]

Add a **1-byte version/algorithm prefix before `CMD_BYTE`** to **every** signing-byte format (OH register/fetch/revoke, AckFetch CMD 156, Renewal CMD 157, and any future signed command):

```
old: [CMD_BYTE | oh_id | field-specific | timestamp | nonce]
new: [VERSION_BYTE | CMD_BYTE | oh_id | field-specific | timestamp | nonce]
```

- This lets MS03 do **dual-version support per command** (verify both the ECDSA-v1 and Ed25519-v2 signing bytes during the transition) instead of a big-bang cutover.
- It should be specified and added **before** MS04/MS05 introduce further signed formats, so the migration surface stops growing.

### 9. Key Separation Requirement — [partly frontend ships]

- Distinct keys for distinct purposes is a hard requirement: cipher key ≠ MAC key (delivered by section 8, **[frontend ships]**); signing key (Ed25519) ≠ encryption key (X25519) at the node and channel level (sections 1, 4, **[remains]**).
- No key may be reused across cipher/MAC/signing roles.

### 10. Remove `k_auth_priv` From the QR Code — [remains]

The channel QR-code JSON currently carries `k_auth_priv` (the channel auth **private** key). Anyone who sees the QR can therefore sign as the channel. The QR must contain **public keys + `K_enc` only**:

- Remove `k_auth_priv` from the QR JSON.
- Keep `k_auth_pub` (and the X25519 enc public key after section 1) and `k_enc`.
- The auth private key must be generated/derived per device and never transmitted in the QR. (If both peers genuinely need the same signing key, that is a design smell to resolve in MS03 — see Open Questions.)

This supersedes the `k_auth_priv` field shown in section 4's QR JSON example above.

### 11. Migration Strategy

Since this is a breaking protocol change:

1. Add a protocol version byte to the handshake (currently `VERSION = 22`; bump to `23`).
2. Full nodes with version 23 support both old (v22) and new (v23) handshakes during a transition period.
3. Light clients upgrade immediately (mobile app update).
4. After transition period, remove v22 support.

## Protobuf Changes

No structural changes to proto files. The `bytes` fields for keys and signatures naturally accommodate the new sizes (32-byte Ed25519 keys, 64-byte Ed25519 signatures).

## Backend Changes

| File | Action |
|------|--------|
| `NodeId.java` | Rewrite: Ed25519 + X25519 dual keypair, new export format, update HashCash PoW |
| `GarlicMessage.java` | Replace AES-CTR + ECDSA with AES-256-GCM + X25519 ECDH + HKDF |
| `ConnectionHandler.java` | Replace AES-CTR stream with framed AES-256-GCM, update handshake to 32-byte keys |
| `OutboundAuth.java` | Replace SHA256withECDSA with Ed25519 verification |
| `OutboundService.java` | Update signing byte verification for Ed25519 |
| `Server.java` | Bump `VERSION` to 23, support dual-version handshake |
| `KadStoreManager.java` | Update signature verification to Ed25519 |

## Mobile Changes

| File | Action |
|------|--------|
| `channel.dart` | Change K_auth to Ed25519 keypair, bump serialization to v3 |
| `redpanda_light_client.dart` | Update handshake to 32-byte keys, framed GCM |
| `database.dart` | Migration v6: update Channels table for new key format |
| `garlic_message_wrapper.dart` | Update to AES-256-GCM + X25519 |
| **New**: `crypto_utils.dart` | Ed25519 sign/verify, X25519 ECDH, HKDF, AES-256-GCM helpers |
| `redpanda_light_client.dart` (v2 envelope) | **[frontend ships]** `[0x02][IV][ciphertext][HMAC]` envelope; HKDF `K_cipher`/`K_mac`; constant-time MAC; inner `ChannelMessage` protobuf |
| `message_sync_service.dart` | **[frontend ships]** dedup per `(channel, message_id)` from the inner plaintext, not the server `MailItem.message_id` |

## Acceptance Criteria

- [x] `NodeId` uses Ed25519 for signing and X25519 for encryption — no brainpoolp256r1 (Backend, redpandaj #221)
- [x] Garlic messages use AES-256-GCM with X25519 ECDH; decrypting a tampered ciphertext fails with auth error (Backend)
- [x] TCP connections use framed AES-256-GCM; a flipped bit in transit causes a decryption failure (not silent corruption) (Backend)
- [x] OH registration/fetch/revoke use Ed25519 signatures (64 bytes, deterministic) (Backend redpandaj #221; Frontend mobile #23)
- [x] No references to RC4, ARCFOUR, or AES/CTR remain in the codebase — Backend: Done bis auf den isolierten, deprecated v22-Legacy-Pfad (siehe [Decisions](#decisions-backend-2026-06-12)); Frontend: Done (mobile #24, `pointycastle` entfernt)
- [x] Protocol version 23 nodes can handshake with version 22 nodes (transition period) — v22 nur noch für Light Clients, siehe [Decisions](#decisions-backend-2026-06-12)
- [x] Channel QR code uses v3 format with Ed25519 K_auth keypair (mobile #23)
- [x] **[frontend ships]** Channel payloads use the v2 envelope `[0x02][IV 16][ciphertext][HMAC-SHA256 32]` (mobile #14) — in MS03 abgelöst durch das GCM-Envelope v3 `[0x03][nonce 12][ciphertext+tag]` (Open Question 6, mobile #23)
- [x] **[frontend ships]** `K_cipher` and `K_mac` are derived from `K_enc` via HKDF-SHA256 with the specified `info` strings; HMAC key ≠ cipher key (mobile #14) — unter GCM (Envelope v3) obsolet: Single-Key-AEAD, keine separate MAC mehr
- [x] **[frontend ships]** MAC covers `[version || IV || ciphertext]` and is verified in constant time (mobile #14) — unter GCM (Envelope v3) ersetzt durch den AEAD-Tag
- [x] **[frontend ships]** Inner plaintext is the `ChannelMessage` protobuf (`message_id` 16 bytes, reused across retries; `timestamp_ms`; `content`) — unverändert auch im Envelope v3
- [x] **[frontend ships]** Receiver dedups per `(channel, message_id)` — retries do not create duplicate messages (mobile #14)
- [x] All signing-byte formats carry a 1-byte version/algorithm prefix before `CMD_BYTE`, enabling dual-version verification per command — Backend-Verifikation (redpandaj #221), Frontend signiert `[0x02 | CMD_BYTE | …]` (mobile #23)
- [x] `k_auth_priv` is absent from the channel QR JSON (mobile #23)
- [x] All existing unit tests pass with the new crypto stack (Backend redpandaj #221; Frontend mobile #23/#24 — inkl. E2E gegen das MS03-Referenz-JAR)

## Decisions (Backend, 2026-06-12)

Umgesetzt in redpandaj [#221](https://github.com/redPanda-project/redpandaj/pull/221). Wire-Formate exakt wie in den Specs (Sektionen 1–3, 5, 8 bzw. [Backend-View](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms03_authenticated_encryption.md)); folgende Festlegungen/Präzisierungen gelten zusätzlich und sind **für Frontend MS03 verbindlich**:

1. **HKDF-SHA256** (BouncyCastle `HKDFBytesGenerator`) für alle Key-Derivations — Info-Strings `"garlic-v2"`, `"tcp-client"`, `"tcp-server"` (Open Question 1). Die per-Richtung-Info-Strings **ersetzen** das in Abschnitt 3 skizzierte `info="tcp-v2"`; verbindlich sind `"tcp-client"`/`"tcp-server"` mit `salt = min/max(verifyKeys)`.
2. **KademliaId = die ersten 20 Bytes von `SHA-256(verifyKey)`** (Bytes 0–19) — Input ist nur der 32-byte Ed25519 Verify-Key, nicht der 64-byte Public-Export (Open Question 4). HashCash-PoW: ≥ 8 führende Nullbits von `SHA256(SHA256(verifyKey))`.
3. **Keine NodeId-Migration** — v23-Nodes erzeugen neue Identitäten (Testnetz, KISS). Deshalb werden **v22-Full-Nodes abgelehnt**; v22 wird nur noch für **Light Clients** (die ausgelieferte Mobile-App) akzeptiert. Der Server bedient v22-Clients mit einer separaten Legacy-Identität (brainpool, `crypt/legacy/LegacyNodeId`) — die ausgelieferte App prüft Version/Key-Bindung nicht und bleibt kompatibel.
4. **Dual-Version-Pfad isoliert + deprecated**: Legacy-Crypto (brainpool/AES-CTR) lebt ausschließlich in `crypt/legacy/` und `LegacyCtrCipherStreams`, alles `@Deprecated(forRemoval = true)`. Abschaltbar über die Konstante `Server.ACCEPT_LEGACY_V22_LIGHT_CLIENTS`; die Übergangsdauer ist eine offene Betriebsentscheidung (Open Question 3).
5. **AES-256-GCM statt ChaCha20-Poly1305** für den TCP-Stream (Open Question 2) — bleibt beim Spec-Wire-Format, KISS.
6. **TCP v23 Details**: „client“ = Verbindungs-Initiator. Frame-Nonce = 96-bit Big-Endian-Counter (4 Nullbytes + uint64), startet bei 0, separat pro Richtung; der Empfänger erzwingt den erwarteten Counter (Replay-/Reorder-Schutz). Max. 32 KiB Plaintext pro Frame. Jeder Auth-/Framing-Fehler beendet die Verbindung. Handshake-Ablauf für Light Clients unverändert (30-byte Magic-Handshake → `REQUEST_PUBLIC_KEY`/`SEND_PUBLIC_KEY` mit 64-byte Export → `ACTIVATE_ENCRYPTION` mit 32-byte ephemeral X25519 Key → erster verschlüsselter Command des Clients ist ein initialer `PING`).
7. **Garlic v2**: Das GMType-Byte verdoppelt als Versions-Byte (`GARLIC_MESSAGE = 0x02`); das v1-Format (`0x01`) wird nicht mehr geparst. Intermediate Nodes prüfen keine Signatur mehr — Authentizität prüft nur der Empfänger via GCM-Tag (AAD = 20-byte Ziel-KademliaId).
8. **Signing-Versions-Byte (§8)**: Ed25519-Signaturen decken `[0x02 | CMD_BYTE | felder | timestamp | nonce]` ab — zentral verifiziert in `OutboundAuth` für alle signierten Commands (register/fetch/revoke/ackFetch). Legacy-ECDSA-Clients (65-byte Key) signieren weiter das unversionierte v1-Format (Dual-Version pro Command). **Frontend: `oh_auth_public_key` = 32-byte Ed25519 Verify-Key** (nicht der 64-byte Export), Signatur = 64 bytes fix.
9. **Updater-Signing-Key**: Der pre-MS03 brainpool-Update-Key ist ungültig; Platzhalter mit Null-Handling (Updates deaktiviert, kein Crash). **Restpunkt**: Core-Entwickler müssen eine neue Ed25519-Identität publizieren.

## Decisions (Frontend, 2026-06-12)

Umgesetzt in mobile [#23](https://github.com/redPanda-project/redpanda-mobile/pull/23) (Primitives,
OH-Auth v2, Channel-Key-Model v3, Garlic v2, Envelope v3, DB-Migration) und
[#24](https://github.com/redPanda-project/redpanda-mobile/pull/24) (Handshake v23, framed GCM,
Identity). Wire-Formate exakt wie Spec + [Backend-Decisions](#decisions-backend-2026-06-12); folgende
Frontend-Festlegungen gelten zusätzlich:

1. **Dart-Crypto-Library = `cryptography`** (^2.7, pure Dart) — `pointycastle` 4.x bietet kein
   Ed25519/X25519 und wurde vollständig entfernt (Open Question 5). Primitives sind gegen die
   RFC-8032/7748/5869-Testvektoren getestet; Interop gegen das Backend via E2E-Suite.
2. **Message-Envelope v3 statt v2** (Open Question 6): Versions-Byte auf `0x03` gebumpt,
   `[0x03][nonce 12][ciphertext + GCM-Tag 16]`, Key = `K_enc` direkt (Single-Key-AEAD — die
   HKDF-`K_cipher`/`K_mac`-Trennung und die separate HMAC des v2-Envelopes entfallen),
   AAD = Channel-ID (hex). Kein v2-Lesepfad: die DB-Migration entfernt Alt-Channels ohnehin.
3. **Channel-Auth-Key bleibt beim Ersteller** (Open Question 7): `Channel.generate()` erzeugt das
   Ed25519-Keypair; nur der Ersteller hält den Private Seed (`authPrivateKey` nullable), der
   Joiner importiert via QR nur `k_auth_pub`. Da aktuell nichts mit K_auth signiert wird, ist
   kein Key-Exchange nötig; sobald Channel-Metadaten-Signaturen kommen (MS03b+), wird auf
   Per-Device-Keys mit gegenseitigem Austausch erweitert. „Kein Private Key im QR" ist erfüllt.
4. **QR v1/v2 werden abgelehnt** (klare Fehlermeldung statt Backward-Compat) und die
   **Drift-Migration v9 ist destruktiv** (Channels/Messages/OH-Handles werden entfernt):
   Breaking Protocol Change im Testnetz, beide Seiten erzeugen den Channel per v3-QR neu.
5. **Light-Client-Identität ohne HashCash-PoW**: Der Server erzwingt das PoW nur für Full-Node-
   Identitäten; die Light-Client-Identität ist ephemer (pro App-Lauf) — kein Key-Grinding im
   Client. KademliaId = `SHA256(verifyKey)[0..20]` wie Decision 2.
6. **Transport-Ordering**: Ein-/ausgehende GCM-Frames laufen pro Verbindung durch serialisierte
   Async-Chains, damit die Counter-Nonces strikt geordnet bleiben; jeder Auth-/Framing-Fehler
   trennt die Verbindung.

## Open Questions

Backend-seitig beantwortet durch die [Decisions (Backend)](#decisions-backend-2026-06-12):

1. ~~Should we use HKDF or a simpler KDF?~~ → HKDF-SHA256 (Decision 1).
2. ~~AES-256-GCM per-frame or ChaCha20-Poly1305?~~ → AES-256-GCM (Decision 5).
3. ~~How long should the v22/v23 dual-version transition period last?~~ → Betriebsentscheidung; technisch per Konstante entfernbar (Decision 4).
4. ~~Should `KademliaId` derivation change?~~ → die ersten 20 Bytes von `SHA-256(verifyKey)` (Decision 2).

Frontend-seitig beantwortet durch die [Decisions (Frontend)](#decisions-frontend-2026-06-12):

5. ~~Dart crypto library choice?~~ → `cryptography` package (Frontend-Decision 1).
6. ~~v2 envelope mit GCM oder Bump auf `0x03`?~~ → Bump auf `0x03`, HMAC entfällt (Frontend-Decision 2).
7. ~~Auth private key per Device ohne QR?~~ → Ersteller hält den Key, Joiner nur Public; Per-Device-Erweiterung bei Bedarf in MS03b+ (Frontend-Decision 3).

Weiterhin offen (nicht MS03-Scope):

8. Should `message_id` be a random 16 bytes or a UUIDv4 — and does the receiver need to bound dedup memory (TTL / max retained ids per channel)? (Aktuell: random 16 bytes, Dedup unbounded in der App-DB.)
