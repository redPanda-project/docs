# Frontend Milestones — Status Overview

> Last updated: 2026-02-21

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
| [MS01](ms01_first_real_message.md) | OH Client & Chat Integration | Stub | Blocked bis Backend MS01 Done |
| [MS02](ms02_reliable_delivery.md) | Retry, Dedup & Polling | Missing | Blocked bis Backend MS02 Done |
| [MS03](ms03_authenticated_encryption.md) | Dart Crypto Migration | Missing | Blocked bis Backend MS03 Done |
| [MS04](ms04_multi_hop_garlic.md) | Garlic Wrapping & Hop Selection | Missing | Blocked bis Backend MS04 Done |
| [MS05](ms05_reverse_garlic.md) | RGB Builder & Session Tags | Missing | Blocked bis Backend MS05 Done |
| [MS06](ms06_two_layer_ack.md) | ACK Handling & Node Scoring | Missing | Blocked bis Backend MS06 Done |
| [MS07](ms07_push_notifications.md) | Push Registration & Background Fetch | Missing | Blocked bis Backend MS07 Done |
| [MS08](ms08_group_chat.md) | Group Chat (rein Frontend) | Missing | Blocked bis Frontend MS05 Done |
| [MS09](ms09_incentive_system.md) | Reputation Client | Missing | Blocked bis Backend MS09 Done |

## Component Readiness

| Component | File | Status |
|-----------|------|--------|
| TCP connection + peer management | `redpanda_light_client.dart` | Done |
| `sendMessage()` | `redpanda_light_client.dart` | Stub — `UnimplementedError` |
| Channel model | `channel.dart` | Done (model only) |
| Chat UI | `chat_screen.dart` | Done — mock auto-reply |
| Database (Drift v5) | `database.dart` | Done |
| Providers (Riverpod) | `providers.dart` | Done |
| Garlic wrapping | `garlic_message_wrapper.dart` | Exists — not called from network layer |
| OH client-side | — | Missing |
| Peer repo injection | `DriftPeerRepository` | Exists — not wired into providers |

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
