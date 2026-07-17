# MS02: Reliable Delivery

## Status: Done — Backend (2026-06-10, redpandaj [#207](https://github.com/redPanda-project/redpandaj/pull/207)), Frontend (2026-06-11, mobile [#13](https://github.com/redPanda-project/redpanda-mobile/pull/13))

`OutboundMailboxStore` uses a sequence-based `BTreeMap` keyed by `sequence_id` per OH, with
`AckFetchRequest`/`deleteUpTo()` implementing delete-after-acknowledge; expired handles are
cleaned up on a periodic job that also wipes their mailbox. The mobile client has a
`SendRetryQueue` (exponential backoff, max 10 attempts) and dedups incoming messages by
`message_id`. See Known limitations for the two aspects still deliberately unhardened.

### Known limitations

- **OH-Auth replay cache is in-memory only**, not persisted across a node restart (accepted
  residual risk). `redpandaj/src/main/java/im/redpanda/outbound/OutboundAuth.java:26-28,47`
- **Message deposit has no sender authentication and no per-sender rate limit** — any peer can
  deposit to any known `oh_id` without a signature. [MS02b](ms02b_oh_discovery_forwarding.md)
  hardened mailbox *capacity* (reject-new eviction, 64 KiB per-item limit, 4 MiB byte quota,
  5/min register-rate-limit) but explicitly deferred a per-sender deposit rate limit /
  deposit-token scheme as a residual attack vector (MS02b
  [Decision 4](ms02b_oh_discovery_forwarding.md#decisions-backend-2026-06-11)). Only
  `registerRateLimited()` (`redpandaj/src/main/java/im/redpanda/outbound/OutboundService.java:407`)
  rate-limits anything; `depositMessage()` (`OutboundService.java:337-349`) does not.

## Goal

Guarantee that every message sent to an Outbound Handle is eventually delivered to the recipient, even if the recipient is offline for hours or days. Messages are delivered exactly once and in order.

## Prerequisites

- MS01 (First Real Message) — basic OH send/receive working

## Current State

| What | Where | Status |
|------|-------|--------|
| Mailbox store | `OutboundMailboxStore.java` | Done — sequence-based `BTreeMap`, delete-after-acknowledge, max 500 items |
| Handle lifecycle | `OutboundHandleStore.java` | Done — TTL clamp 10min–7d, `cleanupExpired()` also wipes the handle's mailbox (10-min job) |
| Fetch pagination | `outbound.proto` → `FetchRequest.cursor` | Done — cursor = `sequence_id`, `AckFetch` implemented |
| Mobile fetch | `redpanda_light_client.dart` | Done (delivered in MS01) |
| Mobile retry | `send_retry_queue.dart` | Done — `SendRetryQueue`, max 10 attempts, exponential backoff |
| Message dedup | `database.dart` (`message_id` UNIQUE) | Done — dedup check before insert |

## Spec

### 1. Sequence-Based Mailbox

Replace the `ArrayList<byte[]>` mailbox with a proper sequence-based queue:

**`OutboundMailboxStore.java`:**
- Each `MailItem` gets a monotonically increasing `sequence_id` (long, per OH).
- Storage: MapDB `BTreeMap<Long, byte[]>` keyed by `sequence_id` per OH (or a single map with composite key `ohId + seqId`).
- `addMessage(ohId, mailItem)`: Assign next `sequence_id`, store.
- `fetchMessages(ohId, afterSequence, limit)`: Return items where `sequence_id > afterSequence`, ordered ascending, capped at `limit`.

### 2. Delete-After-Acknowledge

Add a new command `AckFetch`:

- Client sends `AckFetchRequest { oh_id, acked_sequence_id, timestamp_ms, nonce, signature }`.
- Server deletes all items with `sequence_id <= acked_sequence_id`.
- This ensures the client only deletes messages it has persisted locally.

### 3. OH Lifecycle Management

**Auto-renewal (mobile):**
- Track `expires_at_ms` per OH in Drift.
- When `expires_at_ms - now < 1 day`, re-send `RegisterOhRequest` with a fresh `requested_expires_at`.
- If renewal fails (node unreachable), retry with exponential backoff.

**Cleanup (backend):**
- `OutboundHandleStore.cleanupExpired()` already exists.
- Add: When a handle expires, also delete its mailbox from `OutboundMailboxStore`.
- Schedule cleanup as a periodic job (e.g. every 10 minutes) in `Server.startUpRoutines()`.

### 4. Mobile Retry Logic

**Sending:**
- On `sendMessage()` failure (timeout, connection lost), keep message in Drift with `status: 0` (pending).
- Background retry timer: every 60 seconds, scan for pending messages and retry.
- Max retries: 10, with exponential backoff (1min, 2min, 4min, … capped at 30min).
- After max retries, set `status: 3` (failed permanently) and notify UI.

**Fetching:**
- Background poll every 30 seconds (from MS01).
- On fetch success, persist messages to Drift, then send `AckFetchRequest`.
- On fetch failure, retry next poll cycle.

### 5. Message Deduplication

- Each message has a `message_id` (UUID, set by sender, included in `MailItem`).
- Mobile client: Before inserting a fetched message into Drift, check if `message_id` already exists.
- This handles the edge case where `AckFetch` fails but the message was already persisted.

### 6. Mailbox Overflow Protection

**Backend (`OutboundMailboxStore`):**
- Keep `MAX_ITEMS_PER_MAILBOX = 500` (existing).
- On overflow: Drop oldest (FIFO eviction) — already implemented.
- Add: Return `QUOTA_EXCEEDED` status in `FetchResponse` if mailbox was full when a new message arrived (informational to sender).

**Mobile:**
- If `QUOTA_EXCEEDED` is observed, warn user that some messages may have been dropped.

## Protobuf Changes

**`outbound.proto`:**
```protobuf
// Add to MailItem:
message MailItem {
  bytes message_id = 1;
  int64 received_at_ms = 2;
  bytes payload = 3;
  uint64 sequence_id = 4;  // NEW
}

// Add to FetchResponse:
message FetchResponse {
  Status status = 1;
  uint64 next_cursor = 2;  // now = highest sequence_id returned
  repeated MailItem items = 3;
  int64 server_time_ms = 4;
  bool mailbox_overflow = 5;  // NEW — true if items were dropped
}

// NEW message:
message AckFetchRequest {
  bytes oh_id = 1;
  uint64 acked_sequence_id = 2;
  int64 timestamp_ms = 3;
  bytes nonce = 4;
  bytes signature = 5;
}

message AckFetchResponse {
  Status status = 1;
  int64 server_time_ms = 2;
}
```

## Backend Changes

| File | Action |
|------|--------|
| `OutboundMailboxStore.java` | Replace `ArrayList<byte[]>` with `BTreeMap<Long, byte[]>`, add sequence counter, implement `deleteUpTo(ohId, seqId)` |
| `OutboundService.java` | Add `handleAckFetch()`, wire cleanup on handle expiry |
| `OutboundHandleStore.java` | On `cleanupExpired()`, also call `mailboxStore.deleteAll(ohId)` |
| `InboundCommandProcessor.java` | Register new `CMD_ACK_FETCH` command byte |
| `Server.java` | Add periodic cleanup job for expired handles |

## Mobile Changes

| File | Action |
|------|--------|
| `redpanda_light_client.dart` | Add `ackFetch()`, retry timer, background poll |
| `database.dart` | Add `message_id` column (unique), add `outbound_handles.expires_at` column |
| `chat_screen.dart` | Show message status (pending/sent/failed) icons |
| `providers.dart` | Add `pendingMessagesProvider` for retry queue visibility |

## Acceptance Criteria

- [x] Messages use monotonic `sequence_id`, not list index
- [x] Client sends `AckFetchRequest` after persisting messages; server deletes acknowledged items *(`ackFetch()`, E2E-tested)*
- [x] OH auto-renews before expiry; renewal failure triggers exponential backoff retry *(5-min check, E2E-tested)*
- [x] Failed sends are retried with exponential backoff (up to 10 retries) *(`SendRetryQueue`)*
- [x] Duplicate messages (same `message_id`) are not inserted twice into Drift *(repository check + UNIQUE index)*
- [x] Expired OHs have their mailboxes cleaned up on the server *(`cleanupExpired()` + `deleteAllByHexKey()`)*
- [x] Chat UI shows message delivery status (pending → sent → delivered → failed) *(status icons in `chat_screen.dart`; routed/delivered states landed in MS06)*
- [x] 500-message mailbox overflow drops oldest and sets `mailbox_overflow` flag

## Connection-Notify (2026-07-17)

Real-time "you have new mail" over the **existing peer connection** — no third-party push, no
background wakeup, independent of MS07/D1–D6 (those stay deferred). The node signals a subscribed
client the instant something is deposited into its mailbox; the client then runs a normal signed
fetch. This turns the 30-s fetch poll from the *primary* delivery latency into a *fallback*, without
changing the fetch/ack/cursor/dedup/decrypt path at all.

Two new command *types* extend the outbound command family (150–158) and take **three** command
bytes: `Subscribe` (request/response, 159/160) and the one-way `Notify` (161). Both are **strictly
opt-in**: a notify is only ever sent on a connection that has proven ownership of that `oh_id` via a
signed `Subscribe`. Existing clients never subscribe, so they never receive an unknown command byte
(which would desync their read loop — see MS02b Decision 6).

### Command bytes

| Byte | Command | Direction | Framing |
|------|---------|-----------|---------|
| 159 | `OUTBOUND_SUBSCRIBE_REQ` | Client → Node | `[159][len:4][SubscribeRequest]` |
| 160 | `OUTBOUND_SUBSCRIBE_RES` | Node → Client | `[160][len:4][SubscribeResponse]` |
| 161 | `OUTBOUND_NOTIFY` | Node → Client (one-way) | `[161][len:4][Notify]` |

### 1. Subscribe (request/response)

The client proves OH ownership **exactly like Fetch**: an Ed25519 signature verified against the
`oh_auth_public_key` stored at register time, with the same timestamp window and replay-nonce cache
(`OutboundAuth`). On success the node binds `oh_id → this peer connection`.

**Signing bytes** (the `OutboundAuth` v2 version byte `0x02` is prepended before verification, as
for every signed outbound command):

```
[CMD_BYTE=159 | oh_id | timestamp_ms(8, big-endian) | nonce]
```

- Binding lives only in memory and ends on disconnect — **no persisted subscription state**.
- Multiple OHs may be subscribed on one connection (normal for a multi-channel client).
- Re-subscribe is **idempotent** (re-binds the same `oh_id` to the same connection).
- An `oh_id` is bound to at most one connection; a later successful subscribe from another
  connection replaces the binding (last-writer-wins — the OH owner controls this via its key).

**Error cases** (returned as `SubscribeResponse.status`, mirroring Fetch):

| Status | Cause |
|--------|-------|
| `OK` | Subscribed; node will notify on deposit |
| `BAD_REQUEST` | `oh_id`/`nonce` length out of range |
| `NOT_FOUND` | `oh_id` not registered here or expired |
| `INVALID_SIGNATURE` | signature does not verify against the stored OH key |
| `INVALID_TIMESTAMP` | timestamp outside the ±5-min window |
| `REPLAY` | `(oh_id, nonce)` already seen within the window |

### 2. Notify (one-way, node → client)

On **every successful deposit** into a subscribed mailbox — regardless of deposit path (direct
`FlaschenpostPut`, MS02b `OhForwarder` forwarding, MS04 garlic deliver, MS06 R-ACK) — the node
sends a `Notify` carrying **only the `oh_id`**: no payload, no metadata, no sequence id (D5-analog
minimal-disclosure). It is fire-and-forget: a failed send never affects the deposit. The client
reacts by running a normal signed `FetchRequest` for that `oh_id`; cursor, ack, dedup and decrypt
are unchanged.

### Protobuf Changes

**`outbound.proto`:**

```protobuf
// NEW — Connection-Notify (2026-07-17)
message SubscribeRequest {
  bytes oh_id = 1;
  int64 timestamp_ms = 2;
  bytes nonce = 3;
  bytes signature = 4;   // Ed25519 over [0x02 | 159 | oh_id | timestamp_ms | nonce]
}

message SubscribeResponse {
  Status status = 1;
  int64 server_time_ms = 2;
}

// One-way node → client. Carries ONLY the oh_id — the client fetches to learn what changed.
message Notify {
  bytes oh_id = 1;
}
```

### Decisions (Backend, 2026-07-17)

1. **Ownership proof reuses the Fetch signing scheme** — same `OutboundAuth.verify`, same
   `oh_auth_public_key` stored at register, same timestamp/replay handling. No new key material and
   no new auth path. Subscribe therefore requires the OH to be registered on this node (`NOT_FOUND`
   otherwise), exactly like Fetch.
2. **Subscriptions are in-memory only, per connection, and cleaned up on disconnect** — no
   persistence, no multi-node forwarding of notifies, no batching (KISS). The registry mirrors the
   existing `registerHistory` `WeakHashMap<Peer, …>` pattern so a dead peer's bindings vanish with
   the `Peer` object; the deposit-side lookup additionally drops any binding whose peer is no longer
   connected, so no notify is ever sent to a disconnected client.
3. **Notify carries only the `oh_id`** (no payload/metadata) — the client already has a fully
   signed fetch path; leaking sequence ids or payloads on the notify would only widen the metadata
   surface for the host node. The client's fetch stays the single source of message content.
4. **Notify fires at the `depositMessage` choke point**, so every deposit path (direct, forwarded,
   garlic, R-ACK) triggers it uniformly; a deposit that is *rejected* (quota/oversize) deposits
   nothing and therefore correctly triggers no notify.
5. **Answer to Open Question 3 (real-time overflow signal): keep overflow in `FetchResponse` only,
   no dedicated overflow-notify.** With MS02b reject-new, an overflowing deposit is rejected
   (`QUOTA_EXCEEDED`), stores nothing, and sets the `mailbox_overflow` flag — there is no new item
   to notify about. A subscribed client fetches on the next real deposit's notify (or the fallback
   poll) and sees the flag there. Adding a separate overflow-notify would signal "nothing arrived",
   which is pointless and leaks that the mailbox is full; the connection-notify mechanism answers
   OQ3 by making the *arrival* signal real-time, which is the case that mattered.

### Acceptance Criteria (Connection-Notify)

- [ ] `Subscribe` verifies ownership like Fetch (valid → `OK`; bad sig → `INVALID_SIGNATURE`;
  replayed nonce → `REPLAY`; unknown oh_id → `NOT_FOUND`)
- [ ] A deposit into a subscribed mailbox sends exactly one `Notify(oh_id)` to the subscriber
- [ ] A deposit into a **non**-subscribed mailbox sends no `Notify` (opt-in)
- [ ] Disconnect removes the subscription (no notify to, and no leak of, a dead peer)
- [ ] Re-subscribe is idempotent; multiple OHs per connection work
- [ ] Signing bytes documented: `[CMD_BYTE=159 | oh_id | timestamp_ms(8, big-endian) | nonce]`

## Open Questions

1. Should `AckFetch` be a separate command or piggyback on the next `FetchRequest`?
2. What is the right poll interval — 30s is aggressive for battery; should we use adaptive polling?
3. ~~Should the server notify the client of mailbox overflow in real-time (via the connection), or
   only on the next fetch?~~ **Answered 2026-07-17 (Connection-Notify Decision 5): overflow stays in
   `FetchResponse`; the new `Notify` command makes the message-*arrival* signal real-time.**
4. How to handle the case where a client has multiple OHs on different full nodes — parallel fetch loops?
