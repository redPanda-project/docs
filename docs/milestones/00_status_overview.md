# Milestone Status Overview

> Last updated: 2026-06-11

## Struktur

Die Milestones sind in drei Ebenen aufgeteilt:

| Ebene | Pfad | Beschreibung |
|-------|------|--------------|
| **Gesamt** | `milestones/*.md` (diese Ebene) | Full-Stack Spezifikation pro Milestone |
| **Backend** | [`milestones/backend/`](backend/00_status_overview.md) | Nur Backend-Anteil (Java, Proto, Server-Logik) |
| **Frontend** | [`milestones/frontend/`](frontend/00_status_overview.md) | Nur Frontend-Anteil (Dart, Flutter, Mobile) |

**Reihenfolge**: Backend wird immer **zuerst** umgesetzt, danach Frontend. Jeder Frontend-Milestone ist blocked bis der entsprechende Backend-Milestone Done ist.

```
Backend MSxx fertig → Wire-Format/Proto steht → Frontend MSxx kann starten
```

## Legend

| Symbol | Meaning |
|--------|---------|
| Done | Implemented and functional |
| Partial | Partially implemented / PoC quality |
| Stub | Code exists but throws or returns mock data |
| Missing | Not yet started |

## Milestone Status Matrix

| MS | Title | Backend | Frontend | Gesamt-Spec |
|----|-------|---------|----------|-------------|
| MS01 | First Real Message | [Done (PoC)](backend/ms01_first_real_message.md) | [Done](frontend/ms01_first_real_message.md) | [Full](ms01_first_real_message.md) |
| MS02 | Reliable Delivery | [Done](backend/ms02_reliable_delivery.md) | [Missing](frontend/ms02_reliable_delivery.md) | [Full](ms02_reliable_delivery.md) |
| MS02b | OH Discovery & Forwarding | [Missing](ms02b_oh_discovery_forwarding.md) | [Missing](ms02b_oh_discovery_forwarding.md) | [Full](ms02b_oh_discovery_forwarding.md) |
| MS03 | Authenticated Encryption | [Missing](backend/ms03_authenticated_encryption.md) | [Missing](frontend/ms03_authenticated_encryption.md) | [Full](ms03_authenticated_encryption.md) |
| MS03b | Forward Secrecy | [Missing](ms03b_forward_secrecy.md) | [Missing](ms03b_forward_secrecy.md) | [Full](ms03b_forward_secrecy.md) |
| MS04 | Multi-Hop Garlic | [Partial](backend/ms04_multi_hop_garlic.md) | [Missing](frontend/ms04_multi_hop_garlic.md) | [Full](ms04_multi_hop_garlic.md) |
| MS05 | Reverse Garlic | [Missing](backend/ms05_reverse_garlic.md) | [Missing](frontend/ms05_reverse_garlic.md) | [Full](ms05_reverse_garlic.md) |
| MS06 | Two-Layer ACK | [Missing](backend/ms06_two_layer_ack.md) | [Missing](frontend/ms06_two_layer_ack.md) | [Full](ms06_two_layer_ack.md) |
| MS07 | Push Notifications | [Missing](backend/ms07_push_notifications.md) | [Missing](frontend/ms07_push_notifications.md) | [Full](ms07_push_notifications.md) |
| MS08 | Group Chat | [N/A](backend/ms08_group_chat.md) | [Missing](frontend/ms08_group_chat.md) | [Full](ms08_group_chat.md) |
| MS09 | Incentive System | [Missing](backend/ms09_incentive_system.md) | [Missing](frontend/ms09_incentive_system.md) | [Full](ms09_incentive_system.md) |

## Dependency Graph (Backend → Frontend)

```
Backend MS01 (OH Stabilization) ────────→ Frontend MS01 (OH Client & Chat)
    │                                         │
Backend MS02 (Reliable Mailbox) ────────→ Frontend MS02 (Retry & Dedup)
    │                                         │
Backend MS02b (OH Discovery & Forwarding) ← NEW (oh_id forwarding, OH→node resolution, deposit hardening)
    │
Backend MS03 (Crypto Migration) ────────→ Frontend MS03 (Dart Crypto)
    │                                         │
Backend MS03b (Forward Secrecy) ────────→ Frontend MS03b (Ratchet)  ← NEW (per-message keys, DH ratchet)
    │                                         │
    ├── Backend MS04 (Multi-Hop Relay) ─→ Frontend MS04 (Garlic Wrapping)
    │       │                                 │
    │   Backend MS05 (Reverse Garlic) ──→ Frontend MS05 (RGB Builder)
    │       │                                 │
    │   Backend MS06 (R-ACK Gen) ───────→ Frontend MS06 (ACK Handling)
    │                                         │
    │                                    Frontend MS08 (Group Chat, rein FE)
    │
    └── Backend MS07 (Push Sender) ─────→ Frontend MS07 (Push Registration)

Backend MS09 (Reputation) ──────────────→ Frontend MS09 (Reputation Client)
```

## Component Readiness

### Backend (redpandaj)

| Component | File | Status |
|-----------|------|--------|
| OH Registration / Fetch / Revoke | `OutboundService.java` | Done (PoC) |
| OH Handle persistence (MapDB) | `OutboundHandleStore.java` | Done |
| OH Mailbox persistence (MapDB) | `OutboundMailboxStore.java` | Partial — no delete-after-fetch |
| OH Auth (ECDSA + replay) | `OutboundAuth.java` | Done (PoC) — in-memory replay cache |
| Garlic encryption (single layer) | `GarlicMessage.java` | Done |
| Kademlia DHT | `KadStoreManager.java` | Done (in-memory) |
| TCP + ECDH handshake | `ConnectionHandler.java` | Done |
| Proto definitions | `commands.proto`, `outbound.proto` | Done |

### Mobile (redpanda-mobile)

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
