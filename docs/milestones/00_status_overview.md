# Milestone Status Overview

> Last updated: 2026-06-13

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
| MS02b | OH Discovery & Forwarding | [Done](backend/ms02b_oh_discovery_forwarding.md) | [Done](frontend/ms02b_oh_discovery_forwarding.md) | [Full](ms02b_oh_discovery_forwarding.md) |
| MS03 | Authenticated Encryption | [Done](backend/ms03_authenticated_encryption.md) | [Done](frontend/ms03_authenticated_encryption.md) | [Full](ms03_authenticated_encryption.md) |
| MS03b | Forward Secrecy | [Done](backend/ms03b_forward_secrecy.md) | [Done](frontend/ms03b_forward_secrecy.md) | [Full](ms03b_forward_secrecy.md) |
| MS04 | Multi-Hop Garlic | [Done](backend/ms04_multi_hop_garlic.md) | [Done](frontend/ms04_multi_hop_garlic.md) | [Full](ms04_multi_hop_garlic.md) |
| MS05 | Reverse Garlic | [Done](backend/ms05_reverse_garlic.md) | [Missing](frontend/ms05_reverse_garlic.md) | [Full](ms05_reverse_garlic.md) |
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
Backend MS02b (OH Discovery & Forwarding) ──→ Frontend MS02b (Status-Codes, want_response)  ← Done (Backend 2026-06-11, Frontend 2026-06-12)
    │
Backend MS03 (Crypto Migration) ────────→ Frontend MS03 (Dart Crypto)  ← Done (Backend + Frontend 2026-06-12)
    │                                         │
Backend MS03b (Forward Secrecy) ────────→ Frontend MS03b (Ratchet)  ← Done (2026-06-12: Double Ratchet, Envelope v4)
    │                                         │
    ├── Backend MS04 (Multi-Hop Relay) ─→ Frontend MS04 (Garlic Wrapping)  ← Done (Backend + Frontend 2026-06-12)
    │       │                                 │
    │   Backend MS05 (Reverse Garlic) ──→ Frontend MS05 (RGB Builder)  ← Backend Done (2026-06-13)
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
| OH Auth (Ed25519 + replay) | `OutboundAuth.java` | Done — Ed25519 + Signing-Versions-Byte (MS03), Legacy-ECDSA-Fallback bis v22-Removal |
| Garlic encryption (single layer) | `GarlicMessage.java` | Done — v2: AES-256-GCM + X25519 + HKDF, AAD = Ziel-KademliaId (MS03) |
| Multi-hop garlic relay | `FlaschenpostV2.java`, `GarlicRouter.java` | Done — fixe 2048-B-Pakete, Layer-Peeling, Rebuild + Re-Padding, packet_id-Dedup (MS04); `CMD_DELIVER_TAGGED` mit `session_tag`-Deposit (MS05) |
| Kademlia DHT | `KadStoreManager.java` | Done (in-memory) — Ed25519-Signaturen (MS03) |
| TCP handshake + stream encryption | `ConnectionHandler.java`, `GcmFramedStreams.java` | Done — v23: framed AES-256-GCM, Counter-Nonces; v22 nur noch Light Clients (MS03) |
| Node identity | `NodeId.java` | Done — Ed25519 (sign) + X25519 (encrypt) Dual-Keypair (MS03) |
| Proto definitions | `commands.proto`, `outbound.proto` | Done |

### Mobile (redpanda-mobile)

| Component | File | Status |
|-----------|------|--------|
| TCP connection + peer management | `redpanda_light_client.dart`, `active_peer.dart`, `gcm_framed_codec.dart` | Done — Handshake v23, framed AES-256-GCM mit Counter-Nonces (MS03) |
| `sendMessage()` | `redpanda_light_client.dart` | Done — Envelope v4 via Channel-Ratchet (MS03b), geroutet über 3-Hop-Garlic (`FLASCHENPOST_V2`, MS04) mit Degradierung + direktem MS02b-Fallback (oh_id + want_response, Status-Codes), E2E-tested |
| Channel ratchet (Forward Secrecy) | `ratchet.dart`, `message_crypto_v4.dart` | Done — Double Ratchet (Stage 1+2), Envelope v4 (69 B Overhead), Skipped-Key-Store 512/1024/30 Tage, State-Persistenz on-device (MS03b) |
| Channel model | `channel.dart` | Done — v3: Ed25519 K_auth-Keypair, QR ohne Private Key, Channel-ID = SHA256(K_enc ‖ K_auth_pub) (MS03) |
| Chat UI | `chat_screen.dart` | Done — real sendMessage(), status icons, overflow + deposit-rejection warnings (MS02b) |
| Database (Drift v11) | `database.dart` | Done — Channel-Schema v3 (Ed25519 K_auth), destruktive MS03-Migration; v10 (MS03b): `Channels.ratchetState`; v11 (MS04, nicht destruktiv): `Peers.encryptionPublicKey`; message_id (dedup), retry_count, last_retry_at, last_cursor |
| Providers (Riverpod) | `providers.dart` | Done — incl. mailboxOverflowProvider, pendingMessageCountProvider |
| Send retry queue | `send_retry_queue.dart` | Done — max 10 attempts, exponential backoff (cap 30 min), status-differenziert (MS02b: BAD_REQUEST permanent, QUOTA_EXCEEDED verlängert) |
| Message sync service | `message_sync_service.dart` | Done — dedup persist, cursor/expiry persistence, OH restore on start, ratchet state persist/restore (MS03b) |
| AckFetch + OH renewal | `redpanda_light_client.dart` | Done — CMD 156/157 after fetch, auto-renewal < 1 day, E2E-tested |
| Garlic builder + hop selection | `garlic/garlic_builder.dart`, `garlic/hop_selector.dart` | Done — 3-Layer Flaschenpost v2 (fixe 2048 B, AAD = next_hop), Hop-Auswahl mit Ausschlüssen + Präfix-Diversität (MS04); ersetzt `garlic_message_wrapper.dart` |
| Crypto primitives | `crypto_utils.dart` | Done — Ed25519/X25519/HKDF-SHA256/AES-256-GCM via `cryptography`-Package (MS03) |
| Peer repo injection | `DriftPeerRepository` | Exists — not wired into providers |
