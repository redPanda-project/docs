# MS03b: Forward Secrecy

## Status: Done (2026-06-12 — Backend verifiziert, Frontend mobile [#26](https://github.com/redPanda-project/redpanda-mobile/pull/26); Decisions siehe unten)

**Resolved gap (pre-MS03b):** `K_enc` was a static, lifetime channel key: generated once at channel creation and used unchanged for every message in the channel's history (and it travels in the channel QR). A single key leak — device seizure, backup, a screenshot of the QR — compromised the **entire past and future** of the channel, and any full node that stored encrypted payloads could decrypt them retroactively. MS03 swapped primitives (ECDSA→Ed25519, CTR+HMAC→GCM) but added no ratchet and no ephemeral keys. MS03b closes this with a Double Ratchet (stage 1 + 2 combined, see Decisions); after the first full round trip, message keys contain fresh DH entropy that `K_enc` alone cannot reproduce.

## Goal

Limit the blast radius of a key compromise so that leaking the current key state does **not** expose the whole conversation. Provide forward secrecy in two stages: a pragmatic symmetric ratchet first (stage 1), then a Diffie-Hellman ratchet (stage 2) once the MS03 X25519 primitives are available.

## Prerequisites

- MS03 (Authenticated Encryption) — message-format v2 envelope, HKDF key separation, and (for stage 2) X25519 key exchange primitives

## Current State

| What | Where | Status |
|------|-------|--------|
| Channel key `K_enc` | `channel.dart` | Done — bootstraps the ratchet (Decision 3); no longer used directly as message key |
| Per-message keys | `ratchet.dart` (`RatchetSession`) | Done — `MK_n` from HKDF chains, deleted after use |
| Message counter | v4 envelope header (`chain_counter`, AAD-authenticated) | Done — Decision 2 |
| Symmetric ratchet | `ratchet.dart` | Done — stage 1 chains (`redpanda-fs-msg`/`redpanda-fs-step`) |
| DH ratchet (X25519) | `ratchet.dart` | Done — stage 2, DH step on every fresh inbound ratchet key |
| Ratchet state persistence | `database.dart` (Drift v10, `Channels.ratchetState`) | Done — non-destructive migration, on-device only |

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

- Each message carries an explicit **message counter `n`** (as `chain_counter` in the cleartext, AAD-authenticated v4 envelope header — see Decision 2; the original sketch placed it in the `ChannelMessage` plaintext, which is circular for the DH ratchet) so the receiver knows which `MK_n` to use.
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

> **Superseded by Decision 2 (2026-06-12):** `ChannelMessage` stays unchanged.
> The ratchet fields cannot live in the encrypted plaintext — the receiver
> needs them *before* decryption to derive the message key (circular for the
> DH ratchet). They travel as a cleartext, AAD-authenticated header of the
> new **v4 envelope** instead:
>
> ```
> payload v4 = [0x04][ratchet_pub 32][prev_chain_len 4 BE][chain_counter 4 BE]
>              [nonce 12][ciphertext + GCM tag 16]
> ciphertext = AES-256-GCM(MK_n, nonce, ChannelMessage,
>                          aad = utf8(channelId) ‖ header)
> ```

Original sketch (kept for reference, **not implemented**):

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
| **New**: `ratchet.dart` | Done — `RatchetSession`: symmetric chains (stage 1) + DH ratchet (stage 2), bounded skipped-key store, JSON persistence, commit-on-success state mutation |
| **New**: `message_crypto_v4.dart` | Done — v4 envelope (cleartext, AAD-authenticated ratchet header), 69 bytes fixed overhead |
| `database.dart` | Done — Drift v10 (non-destructive): `Channels.ratchetState`; skipped keys live inside the serialized state (Decision 6) |
| `redpanda_light_client.dart` | Done — v4 encrypt on send, version-byte dispatch v3/v4 on fetch, `ratchetStateUpdates` stream, role/state via `addChannelKeys` |
| `isolate_client.dart` / `isolate_protocol.dart` | Done — role + restored state inbound, advanced state outbound |
| `message_sync_service.dart` | Done — persists advanced ratchet state, restores it with the creator role on startup |
| `chat_screen.dart` | Done — passes creator role (device holds `authPrivateKey`) and persisted state |

## Acceptance Criteria

- [x] **Stage 1:** messages are encrypted with per-message keys derived from an HKDF chain over `K_enc`; each message carries a chain counter
- [x] **Stage 1:** old chain keys and used message keys are securely deleted after advancing — capturing current state does not decrypt earlier messages *(best-effort: Dart cannot zeroize GC memory; state object/DB row only ever hold the current keys — Decision 5)*
- [x] Out-of-order delivery within a chain is handled via a bounded store of skipped message keys (store-and-forward can deliver messages late)
- [x] **Stage 2:** an X25519 DH ratchet step on a fresh inbound ratchet key re-keys the session (post-compromise security)
- [x] Ratchet state is never exported in the QR or any backup that travels off-device
- [x] A long conversation (many messages, some delivered out of order after hours offline) decrypts correctly end-to-end *(unit-tested incl. cross-chain late delivery; E2E suites exchange via v4 against the reference node)*

## Decisions (MS03b, 2026-06-12)

Backend: **Verifikation, keine Code-Änderung** (siehe [Backend-View](backend/ms03b_forward_secrecy.md)) — Deposit/Mailbox/Forwarding behandeln die Payload als opake Bytes; einziges Größenlimit ist `OutboundMailboxStore.MAX_ITEM_BYTES = 64 KiB` pro serialisiertem MailItem.

Frontend umgesetzt in mobile [#26](https://github.com/redPanda-project/redpanda-mobile/pull/26). Folgende Festlegungen gelten:

1. **Stage 1 + Stage 2 kombiniert in einem Format** (Open Question 4): Die MS03-X25519-Primitives sind bereits da, ein Stage-1-Zwischenformat hätte eine zweite Migration erzeugt. Es gibt genau ein Ratchet-Format: **Envelope v4**. Die Stage-1-Ketten (`MK_n = HKDF(CK_n, "redpanda-fs-msg")`, `CK_{n+1} = HKDF(CK_n, "redpanda-fs-step")`) sind die symmetrische Schicht des Double Ratchet, exakt wie in der Spec skizziert.
2. **Ratchet-Header im Envelope statt im `ChannelMessage`-Protobuf**: `ratchet_pub`/`prev_chain_len`/`chain_counter` müssen *vor* der Entschlüsselung lesbar sein (der Key hängt von ihnen ab — die Spec-Platzierung im verschlüsselten Plaintext ist für den DH-Ratchet zirkulär). Sie reisen als Klartext-Header des v4-Payloads, sind aber als Teil der GCM-AAD (`utf8(channelId) ‖ header`) authentifiziert. `ChannelMessage` bleibt unverändert; **Decrypt-Dispatch über das Versions-Byte** (`0x03` → statisches `K_enc` [Lesepfad für Übergang], `0x04` → Ratchet), Senden immer v4.
3. **Initialisierung deterministisch aus `K_enc`, Rollen aus der Channel-Erzeugung** (Open Question 5): kein zusätzlicher Handshake, QR bleibt v3. Ersteller (Gerät mit `authPrivateKey`) startet mit Bootstrap-Keypair (`HKDF(K_enc, "redpanda-fs-boot-ratchet")`) und Bootstrap-Kette (`HKDF(K_enc, "redpanda-fs-chain-init")`) als Sendekette; Joiner generiert ein frisches Keypair und macht den ersten DH-Step gegen den Bootstrap-Public-Key. Bis zum ersten vollen Round-Trip ist der Zustand aus `K_enc` ableitbar (= Status quo vor MS03b für *alle* Nachrichten); danach enthält der Root-Key frische DH-Entropie. `K_enc` aus dem QR zu entfernen bleibt für später (MS03 Frontend-Decision 3 verlangt dafür Per-Device-Key-Exchange).
4. **Out-of-Order-Bounds** (Open Question 2): max. **512** übersprungene Keys pro Advance (`maxSkip`), max. **1024** gespeicherte Skipped-Keys pro Channel (älteste zuerst verdrängt), Expiry **30 Tage**. Größere Lücken/abgelaufene Keys ⇒ Nachricht gilt als unentschlüsselbar (Logeintrag, Batch läuft weiter). Replays (Counter hinter der Kette, Key verbraucht) werden abgelehnt.
5. **Key-Löschung ist Best-Effort**: Dart kann GC-Speicher nicht zuverlässig nullen. „Löschen" heißt: Session-Objekt und persistierte Zeile enthalten nur den *aktuellen* Zustand (+ bounded Skipped-Store); alte Chain-/Message-Keys werden nirgends aufbewahrt. Unit-Tests weisen nach, dass ein Snapshot des Zustands ältere Nachrichten nicht entschlüsselt und ein Klon nach einem Round-Trip ausgesperrt ist.
6. **Persistenz als JSON-Blob** statt eigener Tabellen (KISS): `RatchetSession.toJson()` (inkl. Skipped-Keys) in `Channels.ratchetState`, Drift-Migration **v10, nicht destruktiv**. Der Netzwerk-Layer emittiert nach jedem Advance ein `RatchetStateUpdate`; eine live Session wird nie durch persistierten (älteren) Zustand ersetzt. Eigene Nachrichten kann das Gerät nicht mehr aus der Mailbox entschlüsseln (Standard-Ratchet-Eigenschaft) — irrelevant, da nur der Peer die eigene Mailbox befüllt.
7. **Größenbudget** (für MS04 verbindlich): v4-Festoverhead **69 Bytes** = `1 (Version) + 32 (ratchet_pub) + 4 (PN) + 4 (N) + 12 (Nonce) + 16 (GCM-Tag)`; v3 hatte 29 Bytes (Δ +40). Inneres `ChannelMessage`-Protobuf ≈ 27 Bytes + Content. Bei 2048-Byte-Paketen (MS04-Padding) bleiben damit grob ~1,9 KiB für Content + FlaschenpostPut-Wrapper — unkritisch; Server-Limit 64 KiB pro MailItem.

## Open Questions

1. **Multi-device:** a per-device ratchet means a second device cannot decrypt messages encrypted to the first. How does this interact with a future multi-device milestone (which does not exist yet)? Forward secrecy and multi-device pull in opposite directions. *(Bleibt offen — out of scope, es gibt keinen Multi-Device-Milestone.)*
2. ~~Out-of-order vs. skipped message keys: how many, how long?~~ → 512/1024/30 Tage (Decision 4).
3. **Group chat (MS08):** the Double Ratchet is pairwise. Group chat needs sender keys or pairwise fan-out — how does stage 2 compose with MS08's group model? *(Bleibt offen — MS08 ist out of scope.)*
4. ~~Stage 1 only, or wait for stage 2?~~ → kombiniert in einem Format, keine Doppelmigration (Decision 1).
5. ~~Where does ratchet state initialize from?~~ → deterministisch aus dem QR-`K_enc`, Rollen aus der Channel-Erzeugung; kein Extra-Handshake (Decision 3).
