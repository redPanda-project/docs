# Frontend Milestones — Status Overview

> Last updated: 2026-07-09 (Frontend-MS08 Done)

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
| [MS03b](ms03b_forward_secrecy.md) | Forward Secrecy (Ratchet) | Done | Umgesetzt in mobile PR #26 (2026-06-12): Double Ratchet, Envelope v4, Drift v10 |
| [MS04](ms04_multi_hop_garlic.md) | Garlic Wrapping & Hop Selection | Done | Umgesetzt in mobile PR #29 (2026-06-12), auf Basis Backend MS04 (redpandaj #224) |
| [MS05](ms05_reverse_garlic.md) | RGB Builder & Session Tags | Done | Umgesetzt in mobile PR #33 (2026-07-02), auf Basis Backend MS05 (redpandaj #226) |
| [MS06](ms06_two_layer_ack.md) | ACK Handling & Node Scoring | Done | Umgesetzt in mobile [#37](https://github.com/redPanda-project/redpanda-mobile/pull/37) (2026-07-08), auf Basis Backend MS06 (redpandaj [#229](https://github.com/redPanda-project/redpandaj/pull/229)) |
| [MS07](ms07_push_notifications.md) | Push Registration & Background Fetch | Missing | Blocked bis Backend MS07 Done |
| [MS08](ms08_group_chat.md) | Group Chat (rein Frontend) | Done | Umgesetzt in mobile [#40](https://github.com/redPanda-project/redpanda-mobile/pull/40) (2026-07-09) entlang der sdd05-Decisions (Epochen-Sender-Keys, max. 20 Mitglieder, Envelope v5/v6) — kein Backend-Anteil |
| [MS09](ms09_incentive_system.md) | Reputation Client | Missing | Blocked bis Backend MS09 Done |

## Component Readiness

| Component | File | Status |
|-----------|------|--------|
| TCP connection + peer management | `redpanda_light_client.dart`, `active_peer.dart`, `gcm_framed_codec.dart` | Done — Handshake v23, framed AES-256-GCM mit Counter-Nonces (MS03) |
| `sendMessage()` | `redpanda_light_client.dart` | Done — Envelope v4 via Channel-Ratchet (MS03b), geroutet über 3-Hop-Garlic (`FLASCHENPOST_V2`, MS04) mit Degradierung + direktem MS02b-Fallback (oh_id + want_response, Status-Codes); RGB-Anhang + getaggte Reverse-Replies über pending RGBs (MS05); `CMD_DELIVER_ACKED` mit Return-Path bei eigenem OH (MS06), E2E-tested |
| Chat UI | `chat_screen.dart` | Done — real sendMessage(), deposit-rejection warnings (MS02b), Status-Icons pending→sent(↑)→routed(✓)→delivered(✓✓)/failed(✗) (MS06), Sender-Namen + Member-Count in Gruppen (MS08) |
| Group chat | LC `crypto/group_crypto.dart`, `crypto/group_control.dart`, `domain/group_state.dart`; App `services/group_service.dart`, `screens/group/create_group_screen.dart`, `screens/group/group_info_screen.dart` | Done (MS08) — Epochen-Sender-Keys mit Hash-Chain-FS, Envelope v5 (Gruppennachricht) + v6 (Sealed Control, admin-signiert), Fan-out mit R-ACK je Empfänger, Join-Handshake über 1:1-Kanäle (`ChannelMessage`-Feld 7), Buffer-and-Drain bei Epoch-Mismatch, Receipt-Aggregation (`message_receipts`), max. 20 Mitglieder, E2E-tested |
| ACK handling + node scoring | `garlic/return_path.dart`, `garlic/ack_tag_store.dart`, `garlic/node_scorer.dart`, `domain/routing_ack.dart` | Done (MS06) — `CMD_DELIVER_ACKED` (0x04) + `ReturnPathBlock`, R-ACK-Korrelation über ack_session_tag, `RoutingAck` ohne message_id; Channel-ACK via `ChannelMessage`-Feld 6; NodeScorer (Score + Jitter, `shouldAvoid`) speist HopSelector; E2E-tested |
| Channel ratchet (Forward Secrecy) | `ratchet.dart`, `message_crypto_v4.dart` | Done — Double Ratchet (Stage 1+2), Envelope v4 (69 B Overhead), Skipped-Key-Store 512/1024/30 Tage, State-Persistenz on-device (MS03b) |
| Channel model | `channel.dart` | Done — v3: Ed25519 K_auth-Keypair, QR ohne Private Key, Channel-ID = SHA256(K_enc ‖ K_auth_pub) (MS03) |
| Database (Drift v14) | `database.dart` | Done — Channel-Schema v3 (Ed25519 K_auth), destruktive MS03-Migration; v10 (MS03b): `Channels.ratchetState`; v11 (MS04): `Peers.encryptionPublicKey`; v12 (MS05, nicht destruktiv): `session_tags` + `Channels.pendingRgb`; v13 (MS06, nicht destruktiv): `node_scores`; v14 (MS08, nicht destruktiv): `group_channels`, `group_members`, `group_pending_items`, `group_invites`, `message_receipts`, `Messages.senderMemberId`; message_id (Dedup), retry_count, last_retry_at, last_cursor |
| Providers (Riverpod) | `providers.dart` | Done — includes incomingMessagesProvider, mailboxOverflowProvider, pendingMessageCountProvider; Group-Provider (MS08) |
| Send retry queue | `send_retry_queue.dart` | Done — max 10 Versuche, exponential backoff (cap 30 min), status-differenziert (MS02b: BAD_REQUEST permanent, QUOTA_EXCEEDED verlängert) |
| Message sync service | `message_sync_service.dart` | Done — Dedup-Persist, Cursor/Expiry-Persistenz, OH-Restore beim Start, Ratchet-State-Persist/-Restore (MS03b), Garlic-Session-Persist/-Restore (MS05); R-ACK→routed / Channel-ACK→delivered / Timeout→requeue + Node-Score-Persist/-Restore (MS06); Group-State-Snapshot-Persistenz (MS08) |
| AckFetch + OH renewal | `redpanda_light_client.dart` | Done — CMD 156/157 nach Fetch, Auto-Renewal < 1 Tag, E2E-getestet |
| Garlic builder + hop selection | `garlic/garlic_builder.dart`, `garlic/hop_selector.dart` | Done — 3-Layer Flaschenpost v2 (fixe 2048 B, AAD = next_hop), Hop-Auswahl mit Ausschlüssen + Präfix-Diversität (MS04); `CMD_DELIVER_TAGGED`-Schicht für Reverse-Replies (MS05); `CMD_DELIVER_ACKED` + Score-gewichtete Hop-Auswahl (MS06); ersetzt `garlic_message_wrapper.dart` |
| Reverse garlic (RGB + Session-Tags) | `domain/reverse_garlic_block.dart`, `garlic/rgb_builder.dart`, `garlic/session_tag_store.dart` | Done — RGB pro Nachricht (Hop-Deskriptoren, 24 h Expiry), single-use Tags mit 48-h-Cleanup, Expiry-Fallback auf den Forward-Pfad, Persistenz via `GarlicSessionUpdate`-Snapshots (MS05) |
| OH client-side | `oh_descriptor.dart`, `oh_keypair.dart`, `outbound_handle_repository.dart` | Done — Ed25519 + Signing-Bytes v2 (MS03), register/fetch/sign E2E-tested, isolate-wired, own OH embedded in QR v3 |
| Crypto primitives | `crypto_utils.dart` | Done — Ed25519/X25519/HKDF-SHA256/AES-256-GCM via `cryptography`-Package (MS03) |
| Peer repo injection | `DriftPeerRepository` | Exists — **deliberately not wired** (C4). The network client runs in a background isolate (`RedPandaIsolateClient`), which constructs `RedPandaLightClient` there with the default `InMemoryPeerRepository`. Wiring `DriftPeerRepository` is not a simple provider swap: it needs an `AppDatabase` handle inside the isolate (the DB is opened on the main isolate, so it requires a Drift `DriftIsolate`/connection handoff), and its `getBestPeers`/`knownAddresses` read from an in-memory `_cache` whose load/refresh semantics would have to be defined for cross-isolate use. TODO: pass a `DriftIsolate.connect()` handle through `CmdInit`, reopen it in `_isolateEntryPoint`, then inject `DriftPeerRepository` into the `RedPandaLightClient` ctor. |

## Dependency Graph (Frontend only)

```
[Backend MS01 Done] → Frontend MS01 (OH Client & Chat)
                       └── [Backend MS02 Done] → Frontend MS02 (Retry & Dedup)
                            └── [Backend MS03 Done] → Frontend MS03 (Crypto)
                                 ├── [Backend MS04 Done] → Frontend MS04 (Garlic Wrapping)  ← Done (2026-06-12)
                                 │    └── [Backend MS05 Done] → Frontend MS05 (RGB Builder)  ← Done (2026-07-02)
                                 │         ├── [Backend MS06 Done] → Frontend MS06 (ACK Handling)  ← Done (2026-07-08)
                                 │         └── Frontend MS08 (Group Chat, rein Frontend)  ← Done (2026-07-09)
                                 └── [Backend MS07 Done] → Frontend MS07 (Push)

[Backend MS09 Done] → Frontend MS09 (Reputation Client)
```

## Alignment mit Backend

```
Backend-MS fertig → Proto/Wire-Format steht → Frontend-MS kann starten
```

Siehe [Backend Status Overview](../backend/00_status_overview.md) für den Backend-Gegenstück.
