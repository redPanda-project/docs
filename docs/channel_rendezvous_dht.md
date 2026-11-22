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
reachable. T42 already deposits to k=3 OHs on disjoint nodes and heals a single dead host in-band
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
content = [ 12-byte nonce | AEAD_encrypt( k_enc, nonce, plaintext ) ]      → exactly ONE bucket = 1024 bytes
plaintext = pad_to_fixed_len( { participants: [ { participant_pk, name, oh_list, entry_ts } … ] } )
```

- The value is **opaque** to nodes — they never parse or decrypt it. Only `k_enc` holders read the
  participant list, display names and each participant's current OH list.
- **Single fixed 1024-byte bucket** so the stored/answered record size never reveals which channel is
  being published or resolved (anti-profiling; same rationale as the MS02b fixed-size announce
  record). The bucket is sized from the worst case the redundancy allows: usable plaintext is
  `1024 - 12 nonce - 16 GCM tag = 996` bytes, and two participants with k=3 OH descriptors each
  (`1 + len(endpoint) + 20 handleId + 32 auth_pk` ≈ 74 bytes per descriptor for IPv4) need ≈ 556
  bytes, leaving room for IPv6 endpoints and a third participant. The bucket was 512 bytes while the
  redundancy was k=2; **changing it is a breaking protocol change** — nodes reject every record of a
  different size, so the network must be rolled out before clients publish the new size, and records
  under the old size disappear at rollout (channels heal over the in-band `oh_update` announce
  meanwhile). The **plaintext is padded to a fixed length *inside* the AEAD** before encryption, so the
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
  never-yet-published channel retries faster until a store has first been handed to the
  network at all — i.e. sent with relay hops available; whether it *arrived* is unknowable
  here). The periodic
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

- **`record_store`**: the remote node validates the record (self-certifying signature, exact
  1024-byte size, 48 h TTL) and, if a **global store rate limit** admits it, stores it locally and replicates it
  with a normal `KademliaInsertJob`. Best-effort, **no response** — the client confirms by a later
  lookup. (A per-source limit is not available on the stateless garlic path; a single global cap
  bounds the DHT-write amplification a flood can cause on a node.)
- **`record_lookup`**: the remote node runs the Kademlia search (local store first, then a randomly
  delayed network search) and returns the newest valid record **via the client-chosen `ReturnPath`**
  as a reverse-garlic tagged deliver into the client's **own OH mailbox** — the exact MS05/MS06
  return-path mechanism. Since T162 (redpanda-mobile#155) that return path, like the others into
  the own mailbox, ends at the own mailbox host as a closing hop when its identity is known from
  the client's own handshake; the answer budget over four stations is 1663 B (a channel record
  needs about 1.14 kB). The client correlates the answer with its request via the ack session tag
  and fetches it over the normal signed fetch path. Answer payload:

  ```
  [ 1 status ][ KademliaStore proto if found ]      status: 0 = not found, 1 = found
  ```

  Exactly one answer is always sent (found or not-found), so the client never waits out a timeout for
  a channel that simply has no record yet.

Because the answer arrives as an ordinary `MailItem` (tagged, like an R-ACK), no new client-facing
top-level command exists — the "never send unknown commands unsolicited" rule is satisfied by
construction.

## Decision 4 — Read before write (pairing)

Each channel has exactly ONE rendezvous record per UTC-day key, and a node keeps only the record
with the strictly highest timestamp (`KadStoreManager.put`). Writing the record therefore replaces
every entry the writer did not know. The first client integration wrote first and read afterwards:
a joiner replaced the creator's record with one that carried only itself, and pairing took minutes
instead of seconds (conversation lab, 2026-11-22). Clients follow these rules:

1. **Read before every write without a counterpart entry.** A participant whose merge state holds
   no entry of the counterpart role sends a `record_lookup` for today's key before EVERY publish —
   first, change-driven (own OH set changed) and periodic — whether it created the channel or
   joined it. It holds the publish until a record found under TODAY's key (the record the write
   would replace) is merged, for at most **5 s**, then publishes anyway (counterpart offline, its
   store lost). The held publish writes the MERGED record. If no lookup can be sent (no relay hops,
   no own mailbox), nothing is written; the republish sweep retries. Answers are correlated by the
   lookups' ack tags; the joiner's pairing lookups (rule 3) are registered in an active hold too.
   - "Not found" never ends a hold. Nodes before redpandaj#386 answered a repeated lookup for the
     same key within 30 s with an immediate "not found" without searching (anti-loop blacklist of
     `KademliaSearchJob`), and a genuine "not found" only comes after the DHT search times out
     (5–7 s), i.e. after the hold.
   - A record found under yesterday's key is merged but does not end the hold (it would put
     yesterday's entry over today's); the client asks today's key once more instead.
2. **The single exception** is the creator's first publish at channel creation — triggered by
   registering the channel's first own mailbox, no counterpart known, nothing published before in
   this process. There is nothing to read, and holding it would only widen the window in which a
   quick scan finds no record. The exception is used up by its first attempt, whatever the outcome;
   a retry, and a creator that restarts, comes back online or registers late, reads first like
   everybody else.
3. **Pairing lookups.** A joiner that knows no counterpart mailbox starts, with its first read, a
   bounded lookup sequence independent of failing sends: base delays +2.5, +1.5, +2, +4, +10, +20,
   +40, +80 s (nine lookups in all, ≈ 0 … 160 s), each uniformly jittered by ±40 %, the first one
   never below 2.2 s. It starts only with a lookup that actually went out, and stops as soon as a
   counterpart mailbox is known (from the record or an in-band `oh_update`). The first step waits
   ≥ 2.2 s because the answer is forwarded to the joiner's mailbox by a relay that must resolve the
   mailbox host in the OH-DHT, and a node announces a new registration only 0–2 s after it
   (`OhAnnounceJob.NEW_REGISTRATION_STAGGER_MS`); the answer to the lookup at 0 s is therefore
   often lost. (With the closing host of T162 the answer reaches the mailbox host directly and
   no longer depends on that resolution; the floor stays for return paths that fall back to the
   old shape.)
4. **Two keys, two routes.** Yesterday's key is asked only in the joiner's first round and in
   failing-send recovery, not in the later pairing rounds; it travels on a route of its own that
   avoids the node that executes today's lookup, 0.3–1.5 s after it, so no single node sees both
   UTC-day keys of the channel together. Where no such route exists (a network too small), it
   takes an ordinary route rather than being dropped.
5. **Read-repair.** After merging a resolved record, a participant republishes the merged record
   at once when
   (a) the record is newer than its own last write, carries another participant's entry and lacks
   its own current entry (someone wrote without having seen it), or
   (b) the record is not newer than its own last write and taught it an entry of another
   participant that this write did not carry (a late answer after a timed-out hold: the node still
   holds the write without that entry).
   A newer record that already carries the reader's current entry triggers nothing, so reads do
   not turn into writes.
6. **Monotonic timestamps.** The record timestamp is
   `max(now, newest record timestamp of the same UTC day seen or built + 1)`, capped at
   now + 14 min (the node rejects records more than 15 min in its future,
   `ChannelDht.MAX_FUTURE_SKEW_MS`) and at the last millisecond of the current UTC day: the node
   files a record under the UTC day of its timestamp (`KadContent.createKademliaId`), so a record
   pushed past midnight would be invisible to today's lookups. Seen timestamps beyond now + 15 min
   are ignored. The own entry's `entry_ts` is monotonic over the own previous entry (capped at
   now + 14 min; it does not select a key). A writer whose clock runs behind still writes a
   strictly newer record once it has read the current one — except in the last minutes of a UTC
   day, where the cap may leave it equal and the write loses until the next day's key.
7. **Creator reads once after the first announce.** When a participant learns the counterpart's
   mailboxes in-band (`oh_update`) but has no counterpart entry yet, it sends one (throttled)
   lookup; rule 1 covers its next publish should that lookup be throttled or answered too early.

All merges go per participant id (newest `entry_ts` wins), so entries of further participants in
the same record are carried along by every writer that has read them.

### Announce after adoption

Adopting a counterpart OH set from a resolved record makes the client announce its own full OH set
in-band (`oh_update`) right away: the counterpart found over the DHT has usually never heard of
the reader's mailboxes. A pending announce is kept (not dropped) while no counterpart mailbox is
known and is sent as soon as one is adopted. At most one announce send runs per channel at a time;
an announce queued while one is in flight replaces it and is sent afterwards.

### Privacy / observability

A join adds at most 9 lookups for today's key (plus one repeat when a held read finds only
yesterday's record) and 1 for yesterday's within ≈ 160 s; each travels garlic-wrapped to a remote
exit as before, and the jitter keeps the schedule from pinning the join time beyond the first
lookup. Where the sequence stops still hints at when the pairing succeeded. A participant without
a counterpart entry additionally sends one lookup per publish (≈ one per 3 min while the app
runs) — notably a creator whose channel nobody has joined yet. The node-side lookup limiter (60
per node, refill 1/s) is not approached.

### Known gaps

Narrow cases the rules above do not cover yet: a hold that is open across UTC midnight writes
under the new day's key without having read it; the delayed lookup for yesterday's key computes
its key when it fires, not when its round started; an own `entry_ts` beyond the 14-min cap (clock
set back, restored entry) makes the next own entry lose the merge until the clock catches up; two
overlapping writes for one channel can pick the same record timestamp when the newest seen
timestamp is ahead of the clock; paired sides no longer read, so a clock more than ~3 min ahead on
one side still shadows the other side's writes.

## Acceptance (T43)

- redpandaj: `ChannelDht` primitives (derivation, fixed-size padded records, signature/size/TTL
  validation, newest-wins selection), `CMD_RECORD_STORE` / `CMD_RECORD_LOOKUP` garlic handling with a
  reverse-garlic answer, and a global store rate limiter — with regression tests including the
  store→lookup round-trip.
- Client integration (QR v4 read/write, publish/refresh, recovery lookup, Doctor rendezvous stage)
  is T44; the read-before-write rules of Decision 4 are T160/T161 (redpanda-mobile#154).
