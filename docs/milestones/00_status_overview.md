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

**Spiegel-Sync**: Dieses Repo (`docs`) ist die **Quelle** für alle Milestone-Dateien. Die
Code-Repos spiegeln je ihre Sicht: `redpandaj/milestones/` ← `milestones/backend/`,
`redpanda-mobile/milestones/` ← `milestones/frontend/`. Statusänderungen werden **hier**
gemacht und dann per [`scripts/sync_milestones.sh`](../../scripts/sync_milestones.sh) in die
Code-Repos kopiert (`--check` zeigt Divergenzen, ohne zu kopieren). Direkte Edits an den
Spiegeln divergieren und werden beim nächsten Sync überschrieben.

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
| MS02 | Reliable Delivery | [Done](backend/ms02_reliable_delivery.md) | [Done](frontend/ms02_reliable_delivery.md) | [Full](ms02_reliable_delivery.md) |
| MS02b | OH Discovery & Forwarding | [Done](backend/ms02b_oh_discovery_forwarding.md) | [Missing](frontend/ms02b_oh_discovery_forwarding.md) | [Full](ms02b_oh_discovery_forwarding.md) |
| MS03 | Authenticated Encryption | [Missing](backend/ms03_authenticated_encryption.md) | [Partial](frontend/ms03_authenticated_encryption.md) | [Full](ms03_authenticated_encryption.md) |
| MS03b | Forward Secrecy | [Missing](backend/ms03b_forward_secrecy.md) | [Missing](frontend/ms03b_forward_secrecy.md) | [Full](ms03b_forward_secrecy.md) |
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
Backend MS02b (OH Discovery & Forwarding) ──→ Frontend MS02b (Status-Codes, want_response)  ← Backend Done 2026-06-11
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
| OH Mailbox persistence (MapDB) | `OutboundMailboxStore.java` | Done — sequence-based, delete-after-acknowledge, reject-new + Quotas (MS02b) |
| OH → Node Discovery (DHT) | `OhDht.java`, `OhAnnounceJob.java`, `OhResolveJob.java` | Done — derived announce keys, 256-B-Padding, Jitter |
| OH Forwarding (Option A) | `OhForwarder.java`, `GMParser.java` | Done — oh_id + hop_count erhalten, max. 3 Hops |
| OH Auth (ECDSA + replay) | `OutboundAuth.java` | Done (PoC) — in-memory replay cache |
| Garlic encryption (single layer) | `GarlicMessage.java` | Done |
| Kademlia DHT | `KadStoreManager.java` | Done (in-memory) |
| TCP + ECDH handshake | `ConnectionHandler.java` | Done |
| Proto definitions | `commands.proto`, `outbound.proto` | Done |

### Mobile (redpanda-mobile)

| Component | File | Status |
|-----------|------|--------|
| TCP connection + peer management | `redpanda_light_client.dart` | Done |
| `sendMessage()` | `redpanda_light_client.dart` | Done — AES-256-CTR + HMAC, FlaschenpostPut with oh_id, E2E-tested |
| Channel model | `channel.dart` | Done — v2 with OHDescriptor |
| Chat UI | `chat_screen.dart` | Done — real sendMessage(), status icons, overflow warning |
| Database (Drift v7) | `database.dart` | Done — message_id (dedup), retry_count, last_retry_at, last_cursor |
| Providers (Riverpod) | `providers.dart` | Done — incl. mailboxOverflowProvider, pendingMessageCountProvider |
| Send retry queue | `send_retry_queue.dart` | Done — max 10 attempts, exponential backoff (cap 30 min) |
| Message sync service | `message_sync_service.dart` | Done — dedup persist, cursor/expiry persistence, OH restore on start |
| AckFetch + OH renewal | `redpanda_light_client.dart` | Done — CMD 156/157 after fetch, auto-renewal < 1 day, E2E-tested |
| Garlic wrapping | `garlic_message_wrapper.dart` | Exists — not called from network layer (MS04) |
| Peer repo injection | `DriftPeerRepository` | Exists — not wired into providers |
