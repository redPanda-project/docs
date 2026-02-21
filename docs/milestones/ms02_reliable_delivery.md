# MS02: Reliable Delivery

## Status: Partial

`OutboundMailboxStore` exists with MapDB persistence, but uses index-based cursors, has no delete-after-fetch, and the mobile client has no retry logic.

## Goal

Guarantee that every message sent to an Outbound Handle is eventually delivered to the recipient, even if the recipient is offline for hours or days. Messages are delivered exactly once and in order.

## Prerequisites

- MS01 (First Real Message) — basic OH send/receive working

## Current State

| What | Where | Status |
|------|-------|--------|
| Mailbox store | `OutboundMailboxStore.java` | Done — cursor = list index, no delete-after-fetch, max 500 items |
| Handle lifecycle | `OutboundHandleStore.java` | Done — TTL clamp 10min–7d, `cleanupExpired()` exists |
| Fetch pagination | `outbound.proto` → `FetchRequest.cursor` | Done — but cursor is list index, not sequence ID |
| Mobile fetch | — | Missing |
| Mobile retry | — | Missing |
| Message dedup | — | Missing |

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

- [ ] Messages use monotonic `sequence_id`, not list index
- [ ] Client sends `AckFetchRequest` after persisting messages; server deletes acknowledged items
- [ ] OH auto-renews before expiry; renewal failure triggers exponential backoff retry
- [ ] Failed sends are retried with exponential backoff (up to 10 retries)
- [ ] Duplicate messages (same `message_id`) are not inserted twice into Drift
- [ ] Expired OHs have their mailboxes cleaned up on the server
- [ ] Chat UI shows message delivery status (pending → sent → delivered → failed)
- [ ] 500-message mailbox overflow drops oldest and sets `mailbox_overflow` flag

## Open Questions

1. Should `AckFetch` be a separate command or piggyback on the next `FetchRequest`?
2. What is the right poll interval — 30s is aggressive for battery; should we use adaptive polling?
3. Should the server notify the client of mailbox overflow in real-time (via the connection), or only on the next fetch?
4. How to handle the case where a client has multiple OHs on different full nodes — parallel fetch loops?
