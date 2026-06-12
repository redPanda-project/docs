# MS03: Authenticated Encryption

## Status: Partial (backend Done 2026-06-12, frontend primitive migration open)

> **Backend umgesetzt in redpandaj [#221](https://github.com/redPanda-project/redpandaj/pull/221)** (2026-06-12):
> Ed25519/X25519-NodeId, GarlicMessage v2 (AES-256-GCM), TCP-Handshake v23 mit framed GCM,
> Ed25519 OH-Auth mit Signing-Versions-Byte, Dual-Version-Support v22/v23. Die getroffenen
> Entscheidungen stehen im Abschnitt [Decisions](#decisions-backend-2026-06-12). Frontend MS03
> (Dart-Primitive-Migration, Handshake v23, Channel-Key-Model) kann jetzt starten — siehe
> [Frontend MS03](https://github.com/redPanda-project/docs/blob/main/docs/milestones/frontend/ms03_authenticated_encryption.md).

The codebase used brainpoolp256r1 (ECDH/ECDSA) with AES-CTR (no authentication). The ARC42 spec targets Ed25519 (signatures), X25519 (key exchange), and AES-256-GCM (authenticated encryption) — the backend now implements exactly that.

The **channel message format v2** (section 8 below — versioned `[0x02][IV][ciphertext][HMAC]` envelope, HKDF key separation, inner `ChannelMessage` protobuf, receiver-side dedup) shipped in the frontend (mobile PR #14). The backend primitive migration (Ed25519/X25519/AES-GCM) and the signing version byte shipped in redpandaj #221. What remains is the **frontend** primitive migration. Each spec item below is marked **[frontend ships]** or **[remains]**.

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
- [x] OH registration/fetch/revoke use Ed25519 signatures (64 bytes, deterministic) (Backend; Frontend muss auf Ed25519 + Signing-Versions-Byte umstellen)
- [ ] No references to RC4, ARCFOUR, or AES/CTR remain in the codebase — Backend: Done bis auf den isolierten, deprecated v22-Legacy-Pfad (siehe [Decisions](#decisions-backend-2026-06-12)); Frontend: offen
- [x] Protocol version 23 nodes can handshake with version 22 nodes (transition period) — v22 nur noch für Light Clients, siehe [Decisions](#decisions-backend-2026-06-12)
- [ ] Channel QR code uses v3 format with Ed25519 K_auth keypair
- [ ] **[frontend ships]** Channel payloads use the v2 envelope `[0x02][IV 16][ciphertext][HMAC-SHA256 32]`
- [ ] **[frontend ships]** `K_cipher` and `K_mac` are derived from `K_enc` via HKDF-SHA256 with the specified `info` strings; HMAC key ≠ cipher key
- [ ] **[frontend ships]** MAC covers `[version || IV || ciphertext]` and is verified in constant time
- [ ] **[frontend ships]** Inner plaintext is the `ChannelMessage` protobuf (`message_id` 16 bytes, reused across retries; `timestamp_ms`; `content`)
- [ ] **[frontend ships]** Receiver dedups per `(channel, message_id)` — retries do not create duplicate messages
- [x] **[backend shipped]** All signing-byte formats carry a 1-byte version/algorithm prefix before `CMD_BYTE`, enabling dual-version verification per command — Backend-Verifikation Done (redpandaj #221); Frontend muss `[0x02 | CMD_BYTE | …]` signieren
- [ ] **[remains]** `k_auth_priv` is absent from the channel QR JSON
- [x] **[backend shipped]** All existing unit tests pass with the new crypto stack (Backend; Frontend folgt mit der Primitive-Migration)

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

## Open Questions

Backend-seitig beantwortet durch die [Decisions](#decisions-backend-2026-06-12):

1. ~~Should we use HKDF or a simpler KDF?~~ → HKDF-SHA256 (Decision 1).
2. ~~AES-256-GCM per-frame or ChaCha20-Poly1305?~~ → AES-256-GCM (Decision 5).
3. ~~How long should the v22/v23 dual-version transition period last?~~ → Betriebsentscheidung; technisch per Konstante entfernbar (Decision 4).
4. ~~Should `KademliaId` derivation change?~~ → die ersten 20 Bytes von `SHA-256(verifyKey)` (Decision 2).

Offen (Frontend MS03):

5. Dart crypto library choice: `pointycastle` (pure Dart) vs `cryptography` package (uses platform crypto on iOS/Android)?
6. When the GCM migration (section 2) lands, does the v2 envelope keep version byte `0x02` with GCM replacing CTR+HMAC, or bump to `0x03`? (The HMAC tag becomes redundant under GCM.)
7. If both channel peers need to verify the same `k_auth`, how is the auth private key established per device without ever putting it in the QR — derive both from a shared secret, or give each peer its own signing key with mutual exchange?
8. Should `message_id` be a random 16 bytes or a UUIDv4 — and does the receiver need to bound dedup memory (TTL / max retained ids per channel)?
