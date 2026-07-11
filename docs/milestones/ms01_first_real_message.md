# MS01: First Real Message

## Status: Done — Backend PoC-quality (2026-02-21, redpandaj [#202](https://github.com/redPanda-project/redpandaj/pull/202)), Frontend (2026-06-10, mobile [#9](https://github.com/redPanda-project/redpanda-mobile/pull/9))

Alice and Bob exchange real text messages end-to-end via Outbound Handles: the mobile client
registers an OH, sends via `FlaschenpostPut` (now carried over the MS04+ garlic stack), and
fetches/decrypts messages from its own OH. `chat_screen.dart` shows real messages — no mock
reply remains. The backend keeps its "PoC" qualification because two aspects were deliberately
left unhardened for MS01/MS02b scope reasons; see Known limitations.

### Known limitations

- **OH-Auth replay cache is in-memory only**, not persisted across a node restart (accepted
  residual risk — a captured command could theoretically be replayed within the 5-minute
  window during the seconds around a restart). `redpandaj/src/main/java/im/redpanda/outbound/OutboundAuth.java:26-28,47`
- **Message deposit (`FlaschenpostPut` → OH mailbox) is unauthenticated and unrate-limited** —
  any peer can deposit to any known `oh_id` (best-effort deposit by design; only OH
  *registration* is rate-limited, not deposit). `redpandaj/src/main/java/im/redpanda/outbound/OutboundService.java:337-349`
  vs. `registerRateLimited()` at `OutboundService.java:407`. Deliberately deferred — see
  "Aus MS02b verschoben" in the project workflow rules (authenticated announces,
  deposit rate-limit).

## Goal

Send a text message from Alice's mobile client to Bob's mobile client via the Redpanda network. The message traverses: Alice → Full Node → Bob's Outbound Handle → Bob fetches. Both clients see the message in their chat UI with no mock data.

## Prerequisites

- Running Redpanda full node (seed: `65.109.130.115:59558`)
- Two mobile clients with TCP connectivity to at least one full node

## Current State

| What | Where | Status |
|------|-------|--------|
| OH Register / Fetch / Revoke | `redpandaj/.../outbound/OutboundService.java` | Done (PoC) |
| OH Handle store (MapDB) | `redpandaj/.../outbound/OutboundHandleStore.java` | Done |
| OH Mailbox store (MapDB) | `redpandaj/.../outbound/OutboundMailboxStore.java` | Done — sequence-based, delete-after-acknowledge (MS02) |
| OH Auth (ECDSA + replay) | `redpandaj/.../outbound/OutboundAuth.java` | Done — in-memory replay cache |
| Proto definitions | `redpandaj/.../proto/outbound.proto` | Done |
| Command dispatch | `redpandaj/.../core/InboundCommandProcessor.java` | Done — OH commands routed |
| Mobile TCP + handshake | `redpanda_light_client/lib/src/client/redpanda_light_client.dart` | Done |
| Mobile `sendMessage()` | `redpanda_light_client/lib/src/client/redpanda_light_client.dart` | Done — E2E-tested (no `UnimplementedError` on this path) |
| Mobile Channel model | `redpanda_light_client/lib/src/domain/channel.dart` | Done |
| Chat screen | `redpanda-mobile/lib/screens/chat/chat_screen.dart` | Done — real `sendMessage()`, no mock reply |
| Database | `redpanda-mobile/lib/database/database.dart` | Done (schema v5) |

## Spec

### 1. OHDescriptor Data Model

Define an `OHDescriptor` value object on mobile and backend:

```
OHDescriptor {
  server_endpoint: String   // host:port of the full node hosting this OH
  handle_id: bytes          // 32-byte random OH ID
  oh_auth_public_key: bytes // 65-byte ECDSA public key (brainpoolp256r1)
}
```

Alice and Bob each create one OHDescriptor during channel setup and exchange them (currently via QR code JSON).

### 2. OH Registration (Mobile → Full Node)

Implement `registerOutboundHandle()` in `RedPandaLightClient`:

1. Generate an OH keypair (`NodeId.generateWithSimpleKey()` equivalent in Dart).
2. Build `RegisterOhRequest` protobuf:
   - `oh_id` = 32 random bytes
   - `oh_auth_public_key` = exported public key (65 bytes)
   - `requested_expires_at` = now + 7 days
   - `timestamp_ms` = now
   - `nonce` = 16 random bytes
   - `signature` = ECDSA(SHA256, signing_bytes) where signing_bytes = `[CMD_BYTE | oh_id | requested_expires_at(8) | timestamp(8) | nonce]`
3. Send `[CMD_REGISTER_OH(1 byte)][4-byte length][protobuf]` over the encrypted TCP connection.
4. Parse `RegisterOhResponse`, verify `status == OK`.
5. Persist the OH keypair + `oh_id` + `expires_at_ms` locally in Drift.

### 3. OHDescriptor Sharing via Channel

Extend the channel QR code JSON:

```json
{
  "l": "label",
  "k_enc": "hex...",
  "k_auth": "hex...",
  "oh": {
    "ep": "host:port",
    "id": "hex...",
    "pk": "hex..."
  },
  "v": 2
}
```

When Alice scans Bob's QR code, she stores Bob's OHDescriptor in Drift (new `outbound_handles` table).

### 4. Sending a Message (Alice → Bob's OH)

Implement `sendMessage()` in `RedPandaLightClient`:

1. Look up Bob's OHDescriptor from local DB.
2. Encrypt the plaintext with `K_enc` (AES-256, using the channel's encryption key).
3. Build a `FlaschenpostPut` containing the encrypted payload, targeted to Bob's OH node.
4. If Alice is connected to Bob's OH node → send directly.
5. If not → route via Garlic: wrap in `GarlicMessage` addressed to the OH node's `KademliaId`.
6. On success, insert message into local Drift DB with `status: 1` (sent).

### 5. Fetching Messages (Bob polls his OH)

Implement `fetchMessages()` in `RedPandaLightClient`:

1. Build `FetchRequest` protobuf with OH keypair signature.
2. Send over TCP to the OH full node.
3. Parse `FetchResponse`, iterate `MailItem` list.
4. Decrypt each `payload` with `K_enc`.
5. Insert into local Drift `messages` table.
6. Emit via a `Stream<Message>` so the UI updates reactively.

### 6. Remove Mock Reply from Chat Screen

In `chat_screen.dart`, remove the `Future.delayed` mock reply block (lines marked `// START: Simulate receiving a reply (Mock logic)` to `// END: Mock logic`).

### 7. Wire `sendMessage()` into Chat Screen

In `chat_screen.dart._sendMessage()`:

1. After inserting the message into local DB, call `ref.read(redPandaClientProvider).sendMessage(recipientPublicKey, content)`.
2. On success, update message status to `sent`.
3. On failure, update message status to `failed` and show a snackbar.

### 8. Background Polling

Add a periodic timer (e.g. every 30 seconds) in `RedPandaLightClient` that calls `fetchMessages()` for all registered OHs and emits incoming messages.

## Protobuf Changes

- **No changes to `outbound.proto`** — existing messages are sufficient.
- **`commands.proto`** (mobile copy): Ensure `GarlicMessage` definition is present (it already is).
- **New mobile-side proto** (optional): Consider a `ChannelMessage` wrapper for the encrypted payload format:
  ```protobuf
  message ChannelMessage {
    bytes channel_id = 1;
    bytes ciphertext = 2;
    bytes iv = 3;
    int64 timestamp = 4;
  }
  ```

## Backend Changes

No backend code changes required for MS01. The existing `OutboundService`, `OutboundHandleStore`, `OutboundMailboxStore`, and `OutboundAuth` are sufficient.

## Mobile Changes

| File | Action |
|------|--------|
| `redpanda_light_client.dart` (barrel) | Export new OH client classes |
| `client/redpanda_light_client.dart` | Implement `sendMessage()`, `registerOutboundHandle()`, `fetchMessages()` |
| `client/isolate_client.dart` | Add command forwarding for OH operations |
| `domain/channel.dart` | Add `OHDescriptor` field, bump serialization to `v: 2` |
| `database.dart` | Add `OutboundHandles` table (oh_id, keypair, server_endpoint, expires_at) |
| `chat_screen.dart` | Remove mock reply, wire real `sendMessage()` |
| `providers.dart` | Add `incomingMessagesProvider` stream |

## Acceptance Criteria

- [x] Alice registers an OH on a full node; `RegisterOhResponse.status == OK` *(`registerOutboundHandle()`, E2E-tested)*
- [x] Alice shares her OHDescriptor with Bob via QR code *(Channel QR JSON v2)*
- [x] Bob sends a text message; it arrives at Alice's OH mailbox *(E2E: full-exchange test)*
- [x] Alice fetches the message from her OH; plaintext matches what Bob sent *(E2E-tested)*
- [x] Both Alice and Bob see the conversation in the chat UI with no mock data *(mock reply removed from `chat_screen.dart`)*
- [x] Messages persist across app restarts (Drift DB)
- [x] OH registration auto-renews before expiry *(delivered as part of MS02: 5-min check, E2E-tested — see `00_status_overview.md`)*

## Open Questions

1. Should OH registration happen at app start or lazily on first channel creation?
2. What is the exact AES mode for channel encryption — AES-256-CTR (matching current garlic) or AES-256-GCM (per ARC42 target)?
3. How to handle the case where Alice is not connected to Bob's OH node? Queue locally and retry, or require direct connection for MS01?
4. Should we introduce a `ChannelMessage` protobuf or use raw encrypted bytes in `MailItem.payload`?
