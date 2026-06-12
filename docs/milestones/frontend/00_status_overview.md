# Frontend Milestones — Status Overview

> Last updated: 2026-06-12

Frontend-Milestones werden **nach dem jeweiligen Backend-Milestone** umgesetzt. Das Frontend setzt auf den fertigen Backend-APIs auf.

## Legend

| Symbol | Meaning |
|--------|---------|
| Done | Implemented and functional |
| Partial | Partially implemented / PoC quality |
| Stub | Code exists but throws or returns mock data |
| Missing | Not yet started |

## Milestone Status

| MS | Title | Status | Backend-Abhängigkeit |
|----|-------|--------|----------------------|
| [MS01](ms01_first_real_message.md) | OH Client & Chat Integration | Done | Backend MS01 Done |
| [MS02](ms02_reliable_delivery.md) | Retry, Dedup & Polling | Done | Backend MS02 Done |
| [MS02b](ms02b_oh_discovery_forwarding.md) | OH Discovery & Forwarding (Client-Anteil) | Done | Status-Codes + `want_response` umgesetzt (mobile PR #20, 2026-06-12) |
| [MS03](ms03_authenticated_encryption.md) | Dart Crypto Migration | Done | Umgesetzt in mobile PR #23 + #24 (2026-06-12), auf Basis Backend MS03 (redpandaj #221) |
| [MS03b](ms03b_forward_secrecy.md) | Forward Secrecy (Ratchet) | Missing | MS03 Done — kann starten (Hauptanteil liegt im Client) |
| [MS04](ms04_multi_hop_garlic.md) | Garlic Wrapping & Hop Selection | Missing | Blocked bis Backend MS04 Done |
| [MS05](ms05_reverse_garlic.md) | RGB Builder & Session Tags | Missing | Blocked bis Backend MS05 Done |
| [MS06](ms06_two_layer_ack.md) | ACK Handling & Node Scoring | Missing | Blocked bis Backend MS06 Done |
| [MS07](ms07_push_notifications.md) | Push Registration & Background Fetch | Missing | Blocked bis Backend MS07 Done |
| [MS08](ms08_group_chat.md) | Group Chat (rein Frontend) | Missing | Blocked bis Frontend MS05 Done |
| [MS09](ms09_incentive_system.md) | Reputation Client | Missing | Blocked bis Backend MS09 Done |

## Component Readiness

| Component | File | Status |
|-----------|------|--------|
| TCP connection + peer management | `redpanda_light_client.dart`, `active_peer.dart`, `gcm_framed_codec.dart` | Done — Handshake v23, framed AES-256-GCM mit Counter-Nonces (MS03) |
| `sendMessage()` | `redpanda_light_client.dart` | Done — Envelope v3 (AES-256-GCM, AAD = Channel-ID), FlaschenpostPut with oh_id + want_response, deposit status codes (MS02b), E2E-tested |
| Channel model | `channel.dart` | Done — v3: Ed25519 K_auth-Keypair, QR ohne Private Key, Channel-ID = SHA256(K_enc ‖ K_auth_pub) (MS03) |
| Chat UI | `chat_screen.dart` | Done — real sendMessage(), mock reply removed, deposit-rejection warnings (MS02b) |
| Database (Drift v9) | `database.dart` | Done — Channel-Schema v3 (Ed25519 K_auth), destruktive MS03-Migration; message_id (Dedup), retry_count, last_retry_at, last_cursor |
| Providers (Riverpod) | `providers.dart` | Done — includes incomingMessagesProvider, mailboxOverflowProvider, pendingMessageCountProvider |
| Send retry queue | `send_retry_queue.dart` | Done — max 10 Versuche, exponential backoff (cap 30 min), status-differenziert (MS02b: BAD_REQUEST permanent, QUOTA_EXCEEDED verlängert) |
| Message sync service | `message_sync_service.dart` | Done — Dedup-Persist, Cursor/Expiry-Persistenz, OH-Restore beim Start |
| AckFetch + OH renewal | `redpanda_light_client.dart` | Done — CMD 156/157 nach Fetch, Auto-Renewal < 1 Tag, E2E-getestet |
| Garlic wrapping | `garlic_message_wrapper.dart` | Done (v2-Format: AES-256-GCM + X25519 + HKDF, MS03) — not called from network layer (MS04) |
| OH client-side | `oh_descriptor.dart`, `oh_keypair.dart`, `outbound_handle_repository.dart` | Done — Ed25519 + Signing-Bytes v2 (MS03), register/fetch/sign E2E-tested, isolate-wired, own OH embedded in QR v3 |
| Crypto primitives | `crypto_utils.dart` | Done — Ed25519/X25519/HKDF-SHA256/AES-256-GCM via `cryptography`-Package (MS03) |
| Peer repo injection | `DriftPeerRepository` | Exists — **deliberately not wired** (C4). The network client runs in a background isolate (`RedPandaIsolateClient`), which constructs `RedPandaLightClient` there with the default `InMemoryPeerRepository`. Wiring `DriftPeerRepository` is not a simple provider swap: it needs an `AppDatabase` handle inside the isolate (the DB is opened on the main isolate, so it requires a Drift `DriftIsolate`/connection handoff), and its `getBestPeers`/`knownAddresses` read from an in-memory `_cache` whose load/refresh semantics would have to be defined for cross-isolate use. TODO: pass a `DriftIsolate.connect()` handle through `CmdInit`, reopen it in `_isolateEntryPoint`, then inject `DriftPeerRepository` into the `RedPandaLightClient` ctor. |

## Dependency Graph (Frontend only)

```
[Backend MS01 Done] → Frontend MS01 (OH Client & Chat)
                       └── [Backend MS02 Done] → Frontend MS02 (Retry & Dedup)
                            └── [Backend MS03 Done] → Frontend MS03 (Crypto)
                                 ├── [Backend MS04 Done] → Frontend MS04 (Garlic Wrapping)
                                 │    └── [Backend MS05 Done] → Frontend MS05 (RGB Builder)
                                 │         ├── [Backend MS06 Done] → Frontend MS06 (ACK Handling)
                                 │         └── Frontend MS08 (Group Chat, rein Frontend)
                                 └── [Backend MS07 Done] → Frontend MS07 (Push)

[Backend MS09 Done] → Frontend MS09 (Reputation Client)
```

## Alignment mit Backend

```
Backend-MS fertig → Proto/Wire-Format steht → Frontend-MS kann starten
```

Siehe [Backend Status Overview](../backend/00_status_overview.md) für den Backend-Gegenstück.
