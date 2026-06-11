# MS03b: Forward Secrecy

## Status: Missing

No milestone currently provides forward secrecy. `K_enc` is a static, lifetime channel key: it is generated once at channel creation and used unchanged for every message in the channel's history (and it travels in the channel QR). A single key leak — device seizure, backup, a screenshot of the QR — compromises the **entire past and future** of the channel, and any full node that stored encrypted payloads can decrypt them retroactively. MS03 swaps primitives (ECDSA→Ed25519, CTR+HMAC→GCM) but adds no ratchet and no ephemeral keys. This is the most serious cryptographic design gap, and a messenger marketed for high-risk use should not ship with it open.

## Goal

Limit the blast radius of a key compromise so that leaking the current key state does **not** expose the whole conversation. Provide forward secrecy in two stages: a pragmatic symmetric ratchet first (stage 1), then a Diffie-Hellman ratchet (stage 2) once the MS03 X25519 primitives are available.

## Prerequisites

- MS03 (Authenticated Encryption) — message-format v2 envelope, HKDF key separation, and (for stage 2) X25519 key exchange primitives

## Current State

| What | Where | Status |
|------|-------|--------|
| Channel key `K_enc` | `channel.dart` | Done — static, lifetime key, also in QR |
| Per-message keys | — | Missing |
| Message counter in plaintext | MS03 `ChannelMessage.message_id`/`timestamp_ms` | Partial — id/timestamp exist, no key-chain counter |
| Symmetric ratchet | — | Missing |
| DH ratchet (X25519) | — | Missing — depends on MS03 X25519 |
| "Forward secrecy" in docs | only `08_concepts.adoc` (group-membership rotation, in passing) | Missing as a first-class concept |

## Spec

### Stage 1 (minimum): Symmetric Ratchet over `K_enc`

A one-way HKDF chain derives a fresh per-message key, so an attacker who captures the current chain key cannot decrypt earlier messages.

```
Initial:  CK_0 = HKDF-SHA256(K_enc, salt = empty, info = "redpanda-fs-chain-init")

Per message n (sender and receiver advance in lockstep):
  MK_n = HKDF-SHA256(CK_n, salt = empty, info = "redpanda-fs-msg")     // message key
  CK_{n+1} = HKDF-SHA256(CK_n, salt = empty, info = "redpanda-fs-step") // next chain key
  // derive K_cipher / K_mac (MS03 §7) from MK_n instead of directly from K_enc
```

- Each message carries an explicit **message counter `n`** (in the authenticated `ChannelMessage` plaintext) so the receiver knows which `MK_n` to use.
- After deriving `MK_n` and `CK_{n+1}`, the sender **deletes** `CK_n` and `MK_n`; the receiver deletes `MK_n` and old chain keys once it has advanced past them. Deleting old key material is what provides forward secrecy — without secure deletion the property is only nominal.
- This bounds compromise to messages from the current chain position forward, not the full history. It does **not** provide post-compromise security (future-secrecy / self-healing) — that needs stage 2.

### Stage 2 (target): X25519 DH Ratchet (Double-Ratchet-style)

Once MS03's X25519 primitives land, layer a Diffie-Hellman ratchet on top of the symmetric chains (Signal Double Ratchet model):

- Each side carries a current X25519 ratchet keypair; ratchet public keys ride along in messages.
- On receiving a new ratchet public key, a DH step produces a fresh root key, which seeds new sending/receiving chains.
- This adds **post-compromise security**: after a compromise, a single round-trip of fresh ratchet keys heals the session and locks the attacker out of future messages.
- Sending and receiving chains are the stage-1 symmetric ratchet, reused as the Double Ratchet's symmetric layer.

The QR continues to carry only the long-term channel material (and, after MS03 §10, only public keys + `K_enc`); the ratchet state lives on-device and is never exported.

## Protobuf Changes

```protobuf
// Extend the MS03 ChannelMessage (inner authenticated plaintext):
message ChannelMessage {
  bytes message_id = 1;      // 16 bytes, sender-generated, reused across retries (MS03)
  int64 timestamp_ms = 2;
  string content = 3;
  uint64 chain_counter = 4;  // NEW (stage 1) — message index in the current chain
  bytes ratchet_pub = 5;     // NEW (stage 2) — current X25519 ratchet public key
  uint64 prev_chain_len = 6; // NEW (stage 2) — length of previous chain, for skipped-key handling
}
```

## Backend Changes

| File | Action |
|------|--------|
| — | None. Forward secrecy is end-to-end between channel peers; relays and OH mailboxes store opaque ciphertext and need no changes. |

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `ratchet.dart` | Symmetric chain (stage 1) and DH ratchet (stage 2) state machine; secure key deletion |
| `channel.dart` | Hold ratchet state per channel; derive `MK_n` for encrypt/decrypt instead of using `K_enc` directly |
| `database.dart` | Persist chain keys / ratchet state and a bounded store of skipped message keys |
| `redpanda_light_client.dart` | Encrypt/decrypt via the current message key; advance the ratchet; populate `chain_counter` / `ratchet_pub` |
| `message_sync_service.dart` | Handle out-of-order / skipped messages using retained skipped message keys |

## Acceptance Criteria

- [ ] **Stage 1:** messages are encrypted with per-message keys derived from an HKDF chain over `K_enc`; each message carries a chain counter
- [ ] **Stage 1:** old chain keys and used message keys are securely deleted after advancing — capturing current state does not decrypt earlier messages
- [ ] Out-of-order delivery within a chain is handled via a bounded store of skipped message keys (store-and-forward can deliver messages late)
- [ ] **Stage 2:** an X25519 DH ratchet step on a fresh inbound ratchet key re-keys the session (post-compromise security)
- [ ] Ratchet state is never exported in the QR or any backup that travels off-device
- [ ] A long conversation (many messages, some delivered out of order after hours offline) decrypts correctly end-to-end

## Open Questions

1. **Multi-device:** a per-device ratchet means a second device cannot decrypt messages encrypted to the first. How does this interact with a future multi-device milestone (which does not exist yet)? Forward secrecy and multi-device pull in opposite directions.
2. **Out-of-order vs. skipped message keys:** store-and-forward routinely delivers messages late and out of order. How many skipped message keys are retained, and for how long, before a gap is declared undecryptable? This is a direct trade-off between forward secrecy (delete keys fast) and reliability (keep keys for late deliveries).
3. **Group chat (MS08):** the Double Ratchet is pairwise. Group chat needs sender keys or pairwise fan-out — how does stage 2 compose with MS08's group model, and does MS08's membership-rotation note in `08_concepts.adoc` become the group analogue of this ratchet?
4. Stage 1 only, or wait for stage 2? Stage 1 ships without X25519 (before/alongside MS03 §1) and already removes the worst static-key exposure; stage 2 needs MS03 primitives. Is stage 1 worth shipping as an interim, or does it create a migration we pay for twice?
5. Where does ratchet state initialize from — directly from the QR `K_enc`, or from an initial X25519 handshake at first contact (which would also remove `K_enc` from the QR entirely)?
