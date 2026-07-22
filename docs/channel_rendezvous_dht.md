# Channel Rendezvous over the DHT (QR v4) — Spec Delta

> Spec-delta for the Multi-OH / DHT-Rendezvous work (tasks T42–T44; the implementation plan
> `PLAN-multi-oh-dht.md` lives in the coordination repo, not here).
> Realises the original design sketch in `arc42_archive/redpanda_definitions.md` §1/§6/§8.
> **User decision 2026-07-19: Option B, KISS. No user base ⇒ breaking changes allowed, no
> migration path, no backward compatibility.** QR v3 becomes invalid without replacement.
>
> Backend implemented in redpandaj (T43); mobile QR v4 + rendezvous integration follows in T44.
> This document is a Decisions section in the style of the MS02b / Connection-Notify deltas — not a
> full milestone doc.

## Motivation

A channel today survives only as long as its participants' Outbound Handle (OH) host nodes stay
reachable. T42 already deposits to k=2 OHs on disjoint nodes and heals a single dead host in-band
(`oh_update`). But when **all** of a peer's host nodes are down at once there is no in-band path
left to learn its new OHs. The rendezvous record closes that gap: participants publish their current
OH set into the DHT under a key only channel members can compute, so a channel heals purely over the
DHT even under total communication failure.

## Decision 1 — Channel is a keypair; QR v4 shares the secret

A channel **is** a keypair. The QR code is the only bootstrap and carries the channel **secret**:

```
QR v4 = { v: 4, label: <string>, channel_sk: <32 bytes> }
```

Everything else is derived deterministically, so nothing channel-specific beyond `channel_sk` ever
needs to be shared:

- `channel_pk` — the channel public key (from `channel_sk`).
- **Channel-Id** = `H(channel_pk)` — display / dedup identifier.
- **`k_enc`** = `HKDF-SHA256(channel_sk, …)` — the symmetric key encrypting all channel content
  (rendezvous record value, OH descriptors, channel metadata). Replaces the previously separate
  `K_enc`.
- **Ratchet bootstrap** is re-keyed off the channel secret instead of a separately shared
  `K_auth`: the message ratchet root is derived from `k_enc` (domain-separated HKDF, distinct
  from the record's own use of `k_enc`) and the channel identity keypair (Ed25519, seeded from
  `HKDF-SHA256(channel_sk, info="redpanda.channel.auth.v4")`) provides the role marker driving
  the ratchet asymmetry — a pure function of `channel_sk`, no extra shared secret. Possession of `channel_sk` is the single
  capability that defines a participant (§1 of the definitions: "Teilnehmer ist, wer das
  Channel-Keyset kennt").
- **OH-auth keypairs are deliberately NOT derived from `channel_sk`** (T44 implementation
  decision, supersedes the earlier per-participant-salt sketch): the Ed25519 auth keypair a
  participant registers for an OH (its mailbox on a host node) is **fresh random per OH**.
  Both members hold `channel_sk`, so a derived OH-auth key would let either participant
  compute the private auth key of the *peer's* OHs and fetch/ack (drain) them. With fresh
  keys, OH ownership stays per-device; the peer learns only the public descriptor
  (`endpoint, handle_id, auth_pk`) — carried **inside the encrypted rendezvous record value**
  and the in-band `oh_update`, so the capability boundary is the ciphertext, not the key
  derivation. `channel_sk` still bootstraps everything shared (id, `k_enc`, record key), but
  grants no fetch rights on the peer's mailboxes.

**Every participant holds `channel_sk`** and can therefore read, write and sign channel objects. This
is a deliberate KISS trade-off: the channel has no per-participant write authorisation (any member
can publish; see the record signature below). Group membership is a social property of who was handed
the QR, exactly as in the original sketch.

**QR v3 is invalid without replacement.** There is no user base and no migration path — existing
channels are re-paired with a fresh v4 QR.

## Decision 2 — Rendezvous record: key, signature, encryption

The record reuses the self-certifying `KadContent` DHT primitive (as MS02b's OH-announce record
does), with its **own domain tag** so its keys never collide with node ids or OH-announce records.

### Record signing key (domain-separated, seeded from the secret)

```
recordNodeId = NodeId.fromSeed( SHA256( "redpanda.channel.rendezvous.v1" || channel_sk ) )
```

The signing key is derived from the **secret**, not from `channel_pk`. This is required for
authenticity: the `KadContent` public key is stored in the clear (nodes verify signatures against
it), so a key derived from anything public would let any observer forge records. Deriving from
`channel_sk` means only participants can publish or overwrite a record, while a passive DHT node
still sees only an opaque, domain-separated public key.

### Kademlia key (daily rotation)

```
recordKey(day) = H( dateUTC || recordPubkey )        // KadContent.createKademliaId
```

`recordPubkey` is `recordNodeId`'s public export, so the key carries the domain tag transitively and
rotates with the UTC date like every `KadContent`. Any participant computes it from `channel_sk`;
nobody else can. (The sketch's literal `H(dateUTC || channel_pubkey)` is realised through this
domain-separated `recordPubkey` for the authenticity reason above.)

### Value (opaque ciphertext to nodes)

```
content = [ 12-byte nonce | AEAD_encrypt( k_enc, nonce, plaintext ) ]      → exactly ONE bucket = 512 bytes
plaintext = pad_to_fixed_len( { participants: [ { participant_pk, name, oh_list, entry_ts } … ] } )
```

- The value is **opaque** to nodes — they never parse or decrypt it. Only `k_enc` holders read the
  participant list, display names and each participant's current OH list.
- **Single fixed 512-byte bucket** so the stored/answered record size never reveals which channel is
  being published or resolved (anti-profiling; same rationale as the MS02b fixed-size announce
  record). The **plaintext is padded to a fixed length *inside* the AEAD** before encryption, so the
  ciphertext is itself a constant size — there is **no cleartext length field**. Exposing the real
  payload length outside the AEAD would leak the participant/OH-list count and defeat the padding, so
  all length/padding metadata lives in the encrypted plaintext. The 12-byte nonce is the only
  cleartext framing and carries no size information.
- **Signature**: the whole `KadContent` is signed by `recordNodeId` (the channel record key).
- **TTL 48 h**: nodes reject records older than 48 h (+ small rotation slack) at store and serve
  time. Records rotate under the UTC-day key, so a record published late yesterday stays usable
  through today.
- **Republish**: each participant republishes **on every change of its own OH set** and as a
  **periodic best-effort refresh** (an implementation constant, currently 3 min; a
  never-yet-published channel retries faster until its first successful send). The periodic
  refresh is required because `record_store` is fire-and-forget (no response) and the client
  marks a record as published on *send*, not on arrival: a store dropped in transit goes
  unnoticed until the next refresh, so the interval is the worst-case blind window in which
  a channel stays undiscoverable — records must also re-appear under each new UTC-day key.
  Stretching the interval to 30 min for cadence privacy was tried (2026-07-22) and reverted
  for exactly that reason.
  Trade-off (accepted): nodes holding the record observe an opaque per-channel publish
  cadence — an online signal for "some participant". Heal latency wins over that
  observability; anything ≪ the 48 h TTL is functionally safe, and lookups also check
  yesterday's key, so day rotation leaves no gap.

### Newest-wins per participant

Each participant signs a record snapshot that includes its **own** entry with a fresh `entry_ts`.
Because all members share the record key, several equally-valid records for one channel can coexist
in the DHT. Resolution merges them **per participant, newest `entry_ts` wins** — a **client-side
merge** (T44). At the node/DHT layer the standard single-value newest-`KadContent`-timestamp-wins per
key applies; the per-participant merge lives in the client precisely because a node cannot read the
opaque value.

## Decision 3 — DHT access stays off the light client (`record_store` / `record_lookup`)

Light clients remain **DHT-fremd**: they never run Kademlia themselves. Two operations let a client
ask a node to do it, both delivered **garlic-wrapped to a *remote* node** so the directly connected
node never sees the query interest. They are new **garlic layer commands** (final-hop, alongside
`CMD_DELIVER` = 0x02 …), not new top-level peer commands — so no existing client ever receives an
unknown top-level command byte and desyncs.

| Layer cmd | Value | Plaintext layer |
|-----------|-------|-----------------|
| `CMD_RECORD_STORE`  | `0x05` | `[1 cmd][4 len][KademliaStore proto]` |
| `CMD_RECORD_LOOKUP` | `0x06` | `[1 cmd][20 recordKey][ReturnPath]` |

- **`record_store`**: the remote node validates the record (self-certifying signature, exact 512-byte
  size, 48 h TTL) and, if a **global store rate limit** admits it, stores it locally and replicates it
  with a normal `KademliaInsertJob`. Best-effort, **no response** — the client confirms by a later
  lookup. (A per-source limit is not available on the stateless garlic path; a single global cap
  bounds the DHT-write amplification a flood can cause on a node.)
- **`record_lookup`**: the remote node runs the Kademlia search (local store first, then a randomly
  delayed network search) and returns the newest valid record **via the client-chosen `ReturnPath`**
  as a reverse-garlic tagged deliver into the client's **own OH mailbox** — the exact MS05/MS06
  return-path mechanism. The client correlates the answer with its request via the ack session tag
  and fetches it over the normal signed fetch path. Answer payload:

  ```
  [ 1 status ][ KademliaStore proto if found ]      status: 0 = not found, 1 = found
  ```

  Exactly one answer is always sent (found or not-found), so the client never waits out a timeout for
  a channel that simply has no record yet.

Because the answer arrives as an ordinary `MailItem` (tagged, like an R-ACK), no new client-facing
top-level command exists — the "never send unknown commands unsolicited" rule is satisfied by
construction.

## Acceptance (T43)

- redpandaj: `ChannelDht` primitives (derivation, fixed-size padded records, signature/size/TTL
  validation, newest-wins selection), `CMD_RECORD_STORE` / `CMD_RECORD_LOOKUP` garlic handling with a
  reverse-garlic answer, and a global store rate limiter — with regression tests including the
  store→lookup round-trip.
- Client integration (QR v4 read/write, publish/refresh, recovery lookup, Doctor rendezvous stage)
  is T44.
