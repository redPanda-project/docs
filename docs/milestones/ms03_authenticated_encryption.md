# MS03: Authenticated Encryption

## Status: Missing

The codebase uses brainpoolp256r1 (ECDH/ECDSA) with AES-CTR (no authentication). The ARC42 spec targets Ed25519 (signatures), X25519 (key exchange), and AES-256-GCM (authenticated encryption). RC4 references still exist in the codebase.

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
- QR code JSON: `{"l":..., "k_enc":..., "k_auth_pub":..., "k_auth_priv":..., "v":3}`.

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

### 7. Migration Strategy

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

## Acceptance Criteria

- [ ] `NodeId` uses Ed25519 for signing and X25519 for encryption — no brainpoolp256r1
- [ ] Garlic messages use AES-256-GCM with X25519 ECDH; decrypting a tampered ciphertext fails with auth error
- [ ] TCP connections use framed AES-256-GCM; a flipped bit in transit causes a decryption failure (not silent corruption)
- [ ] OH registration/fetch/revoke use Ed25519 signatures (64 bytes, deterministic)
- [ ] No references to RC4, ARCFOUR, or AES/CTR remain in the codebase
- [ ] Protocol version 23 nodes can handshake with version 22 nodes (transition period)
- [ ] Channel QR code uses v3 format with Ed25519 K_auth keypair
- [ ] All existing unit tests pass with the new crypto stack

## Open Questions

1. Should we use HKDF or a simpler KDF (e.g. SHA-256 of shared secret)? HKDF is more standard but adds complexity.
2. For the TCP stream, should we use AES-256-GCM per-frame or a stream AEAD like ChaCha20-Poly1305? ChaCha is faster on mobile without AES-NI.
3. How long should the v22/v23 dual-version transition period last?
4. Should `KademliaId` derivation change (currently `SHA-256(publicKey)[0:20]`)? With Ed25519 the public key is only 32 bytes vs 65.
5. Dart crypto library choice: `pointycastle` (pure Dart) vs `cryptography` package (uses platform crypto on iOS/Android)?
