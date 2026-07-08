# MS08: Group Chat

## Status: Missing

ARC42 hints at multi-OH fan-out. No group chat code exists. The current channel model is 1-to-1 only.

> **Fan-out model decided** (sdd05 spike, 2026-07-08): sender-side fan-out with
> epoch sender keys — see [Decisions](#decisions-fan-out-spike-sdd05-2026-07-08).
> The spec sections below predate the spike; where they conflict, the Decisions
> section is authoritative.

## Goal

Support group conversations with 3+ participants. Each participant has their own OH. Messages are fan-out encrypted and delivered to each member's OH. Group membership can change (add/remove members) with key rotation to preserve forward secrecy.

## Prerequisites

- MS05 (Reverse Garlic) — reply paths for group members
- MS02 (Reliable Delivery) — each member's OH must reliably deliver

## Current State

| What | Where | Status |
|------|-------|--------|
| Channel model | `channel.dart` — K_enc + K_auth, 1-to-1 | Done — no group support |
| Channels table | `database.dart` — single `encryptionKey`, `authenticationKey` | Done — 1-to-1 schema |
| Chat screen | `chat_screen.dart` — shows one peer | Done — no member list |
| Fan-out | — | Missing |
| Key rotation | — | Missing |

## Spec

### 1. Group Channel Model

Extend `Channel` to support multiple members:

```
GroupChannel extends Channel {
  label: String
  group_id: bytes[32]              // Random group identifier
  encryption_key: bytes[32]        // Shared AES-256 key (rotates on membership change)
  auth_keypair: Ed25519Keypair     // For signing group metadata
  members: List<GroupMember>
  key_epoch: uint32                // Increments on each key rotation
}

GroupMember {
  member_id: bytes[32]             // Unique per member
  display_name: String
  oh_descriptor: OHDescriptor     // Where to send messages for this member
  encryption_public_key: bytes[32] // X25519 public key for key distribution
  role: enum { ADMIN, MEMBER }
}
```

### 2. Group Creation

1. Creator generates `group_id`, initial `encryption_key`, `auth_keypair`.
2. Creator is the first member with role `ADMIN`.
3. Creator shares the group channel via QR code or invite link (encrypted with the invitee's public key).

### 3. Member Addition

1. Admin creates an `GroupInvite` containing the current `encryption_key`, `group_id`, member list with OHDescriptors, and `key_epoch`.
2. Invite is encrypted with the new member's X25519 public key (only they can read it).
3. Admin sends the invite via the new member's OH (using their OHDescriptor from a 1-to-1 channel or out-of-band).
4. New member accepts → sends their OHDescriptor to all existing members.
5. Admin triggers key rotation (new `encryption_key`, incremented `key_epoch`).

### 4. Member Removal

1. Admin removes a member from the local member list.
2. Admin triggers key rotation (the removed member doesn't receive the new key).
3. Admin sends a `MemberRemoved` control message to all remaining members with the new key (encrypted with each member's X25519 public key individually).
4. Remaining members update their local group state.

### 5. Fan-Out Sending

When a member sends a message to the group:

1. Encrypt the message with the group's `encryption_key` (AES-256-GCM).
2. For each other member in the group:
   a. Build a garlic packet addressed to that member's OH (via MS04 multi-hop).
   b. Include an RGB (MS05) for the return path.
3. Send all packets (fan-out).

**Optimization:** If multiple members share the same OH node, batch the delivery.

### 6. Key Rotation

Triggered on: member addition, member removal, periodic (e.g. every 7 days), or manual.

**Rotation protocol:**
1. Admin generates a new `encryption_key` and increments `key_epoch`.
2. Admin sends a `KeyRotation` control message to each member individually, encrypted with that member's X25519 public key:
   ```
   KeyRotation {
     group_id: bytes
     new_encryption_key: bytes[32]
     new_key_epoch: uint32
     member_list: List<GroupMember>  // current membership
   }
   ```
3. Each member stores the new key and marks the old key as "read-only" (for decrypting old messages).
4. Messages encrypted with an old `key_epoch` are still decryptable but flagged as "old key."

### 7. Control Messages

Group management uses typed control messages within the `ChannelMessage` envelope:

```
ChannelMessage {
  message_id: bytes
  oneof body {
    bytes content = 2;           // regular text
    ChannelAck ack = 6;          // from MS06
    GroupControl control = 7;    // NEW
  }
  ...
}

GroupControl {
  oneof action {
    MemberAdded member_added = 1;
    MemberRemoved member_removed = 2;
    KeyRotation key_rotation = 3;
    GroupInfoUpdate info_update = 4;  // name, avatar
  }
}
```

## Protobuf Changes

```protobuf
message GroupMember {
  bytes member_id = 1;
  string display_name = 2;
  bytes oh_endpoint = 3;        // serialized OHDescriptor
  bytes encryption_public_key = 4;
  uint32 role = 5;              // 0=ADMIN, 1=MEMBER
}

message GroupInvite {
  bytes group_id = 1;
  bytes encryption_key = 2;
  uint32 key_epoch = 3;
  repeated GroupMember members = 4;
  string group_name = 5;
}

message GroupControl {
  oneof action {
    MemberAdded member_added = 1;
    MemberRemoved member_removed = 2;
    KeyRotation key_rotation = 3;
    GroupInfoUpdate info_update = 4;
  }
}

message MemberAdded {
  GroupMember member = 1;
}

message MemberRemoved {
  bytes member_id = 1;
}

message KeyRotation {
  bytes new_encryption_key = 1;
  uint32 new_key_epoch = 2;
  repeated GroupMember members = 3;
}

message GroupInfoUpdate {
  string name = 1;
  bytes avatar = 2;
}
```

## Backend Changes

No backend changes required. The OH service is group-agnostic — it just stores and delivers opaque encrypted payloads. Fan-out is handled entirely by the sender's client.

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `group_channel.dart` | GroupChannel model extending Channel |
| **New**: `group_service.dart` | Create/join/leave group, fan-out sending, key rotation logic |
| **New**: `group_invite_screen.dart` | UI for creating/accepting group invites |
| **New**: `group_info_screen.dart` | Member list, admin controls (add/remove/rename) |
| `channel.dart` | Add `isGroup` flag or subtype |
| `database.dart` | Add `group_members` table, `key_epochs` table |
| `chat_screen.dart` | Show sender name per message, member count in header |
| `providers.dart` | Add `groupServiceProvider`, `groupMembersProvider` |
| `redpanda_light_client.dart` | Add `sendGroupMessage()` with fan-out logic |

## Acceptance Criteria

Adjusted by the sdd05 spike (Decisions 8, 10 — QR invite and the explicit key
request are descoped for v1):

- [ ] A group can be created with 3–20 members
- [ ] All members receive messages sent to the group
- [ ] Adding a member triggers key rotation; the new member cannot read pre-join messages
- [ ] Removing a member triggers key rotation; the removed member cannot read new messages
- [ ] Group admin can rename the group; change propagates to all members
- [ ] Fan-out sends messages to each member's OH independently
- [ ] Messages display the sender's name in the group chat UI, and sender authenticity is cryptographically verified (Ed25519 per message)
- [ ] Group invite travels over an existing 1:1 channel (two-way handshake; QR group invites are out of scope for v1)
- [ ] Offline members receive messages when they come back online (via OH mailbox)
- [ ] Key epoch mismatch buffers the affected items locally and drains the buffer once the (reliably re-sent) rotation arrives — no explicit key request protocol in v1

## Decisions (Fan-out-Spike sdd05, 2026-07-08)

Ergebnis des sdd05-Spikes (Gate für Frontend-MS08, Review-Finding S7). Folgende
Festlegungen sind **für Frontend MS08 verbindlich**; sie beantworten die Open
Questions 1–6 unten und übersteuern die älteren Spec-Sketches oben.

### Kostenmodell (Spike-Aufgabe 1)

Konstanten aus MS03b–MS06: Garlic-Paket fix 2048 B (+ 5 B Command-Framing =
2053 B pro Send), Payload-Budget @ 3 Hops: 1764 B (DELIVER) / 1748 B (TAGGED) /
1554 B (ACKED, Tag + 3 Return-Hops). Sender-Upstream **pro Gruppennachricht**
(n Mitglieder ⇒ n−1 Zustellungen, R-ACK angefordert):

| Modell | n=5 | n=20 | n=50 | Krypto/Nachricht | Join (Msgs) | Leave (Msgs) |
|--------|-----|------|------|------------------|-------------|--------------|
| (1) Naiver Fan-out, statischer Gruppenkey (Ist-Sketch) | 8,0 KiB | 38,1 KiB | 98,2 KiB | 1× AES | ≈ 2n | n−2 |
| (2) Sender-Keys, Epochen-Variante (**gewählt**) | 8,0 KiB | 38,1 KiB | 98,2 KiB | 2× AES + 1 Chain-Advance + 1 Ed25519-Sig | n + 2 | n − 2 |
| (2′) Sender-Keys, volles Signal-Modell | 8,0 KiB | 38,1 KiB | 98,2 KiB | wie (2) | n + 2 | **O(n²)** netzweit |
| (3) Multi-OH-Fan-out-Node (Backend) | 2,0 KiB | 2,0 KiB | 2,1 KiB | wie (2) | ≈ n | ≈ n |

Netz-Gesamtvolumen (Paket durchläuft Submit-Link + 3 Relay-Links, R-ACK-Onion
≈ 3 Links): Modelle 1/2 ≈ (n−1) × 14 KiB pro Nachricht (n=20: ≈ 270 KiB);
Modell 3 spart davon nur den Sender-Upstream — die n−1 Zustell-Pakete entstehen
beim Fan-out-Node genauso. Retry-Worst-Case: × 10 (Retry-Queue-Cap) je
Empfänger, dedupliziert über `message_id`.

### Metadaten-Matrix (Spike-Aufgabe 2, gegen Threat-Klassen B/C)

| Beobachter | Modell 1 | Modell 2 (gewählt) | Modell 3 |
|------------|----------|--------------------|----------|
| Empfänger-OH-Host | Deposit-Rate der Mailbox; Sender anonym (3 Hops), Größe fix 2048 B. Sender-Pseudonyme wären ohne Outer-Layer sichtbar | wie 1, aber Envelope v5 versteckt Sender-Pseudonym + Counter hinter K_out (nur Epoche 4 B im Klartext) | wie 2 |
| Relays | nichts (Layer-Peeling, fixe Größe) | nichts | nichts |
| Submit-Node des Senders | Burst von n−1 gleich großen Paketen ⇒ Gruppengrößen-Hinweis (Restrisiko, dokumentiert; Mitigation Jitter/Batching = v2) | wie 1 | 1 Paket, aber … |
| Fan-out-Node | — | — | **Gruppengröße, alle Mitglieds-OH-IDs, Timing jeder Nachricht** — komplette Gruppen-Metadaten an einem Punkt |
| Entferntes Mitglied | bis Rotation alles; danach nichts Neues | wie 1; zusätzlich Hash-Chain-FS innerhalb der Epoche | wie 1/2 |

Modell 3 ist der erwartete K.-o.: es verletzt Threat-Klasse B (honest-but-
curious Node) strukturell und macht Klasse C (Traffic-Analyse) trivial;
zusätzlich bräuchte es einen Backend-Milestone plus MS09-Ökonomie (wer bezahlt
den Fan-out?). Es bleibt als spätere Option für n > 20 dokumentiert — dann mit
eigenem Metadaten-Design.

### Festlegungen

1. **Modell 2: Sender-seitiger Fan-out mit Epochen-Sender-Keys.** Krypto- und
   Key-Management O(1) pro Nachricht, Transport bewusst O(n) bei n ≤ 20.
   Modell 1 abgelehnt: ein statischer symmetrischer Gruppenkey hat keine
   Sender-Authentizität (jedes Mitglied kann jedes andere fälschen) und keine
   Forward Secrecy innerhalb einer Epoche. Modell 3 abgelehnt (s. o.).
   **Backend MS08 bleibt N/A** — der OH-Service bleibt group-agnostic.
2. **Maximale Gruppengröße 20 in v1 (hart erzwungen); Gruppen > 50 sind out of
   scope für MS08.** 19 × 2053 B ≈ 38 KiB Upstream pro Nachricht ist die
   akzeptierte Mobile-Obergrenze (Spike-Tabelle).
3. **Epochen-Sender-Keys statt volles Signal-Modell:** pro Epoche `e` erzeugt
   der Admin ein 32-B `group_secret_e`. Chain-Seeds aller Mitglieder werden
   deterministisch abgeleitet — `ck_0(M) = HKDF(group_secret_e,
   info = "ms08-chain-v1" ‖ member_id)` — sowie der Outer-Key
   `K_out = HKDF(group_secret_e, "ms08-outer-v1")`. Nach Ableitung wird
   `group_secret_e` **gelöscht**; ab da gilt Hash-Chain-Forward-Secrecy pro
   Nachricht (`MK_N = HKDF(ck_N, "ms08-msg-v1")`,
   `ck_{N+1} = HKDF(ck_N, "ms08-adv-v1")`, verbrauchte Keys werden gelöscht).
   Eine Rotation verteilt damit genau **ein** Secret über n Sealed Boxes (O(n))
   statt O(n²) Einzel-Redistributionen beim vollen Signal-Modell. Tradeoff
   (dokumentiert): zwischen Empfang und Install der Rotation ist die Epoche
   auf dem Gerät im Klartext; FS beginnt mit der Löschung.
4. **Sender-Authentizität via gruppen-spezifischem Ed25519:**
   `member_id` **ist** der Ed25519-Verify-Key des Mitglieds (32 B, pro Gruppe
   frisch generiert — keine Verkettbarkeit zwischen Gruppen). Jede
   Gruppennachricht ist signiert; die Signatur ist Teil des inneren Envelopes.
5. **Envelope v5 (Gruppennachricht),** dispatcht wie v3/v4 über das
   Versions-Byte: outer `[0x05][key_epoch 4 BE][nonce 12][ct+tag]` mit
   `K_out`, AAD = utf8(lowercase-hex group_id) — versteckt Sender-Pseudonym
   und Counter vor dem OH-Host (Metadaten-Matrix). Inner (= outer-Plaintext):
   `[member_id 32][N 4 BE][nonce 12][ct+tag][sig 64]` mit `MK_N`;
   inner AAD = utf8(group_id) ‖ epoch(4 BE) ‖ member_id ‖ N(4 BE);
   `sig` = Ed25519 über AAD ‖ nonce ‖ ct. Fester Overhead 161 B; mit innerem
   `ChannelMessage` (≈ 27 B + Content) bleiben ≈ **1,33 KiB Content-Budget**
   bei ACKED @ 3 Forward- + 3 Return-Hops (1554 B, MS06 Decision 6).
6. **Envelope v6 (Sealed Control):** `[0x06][eph_pub 32][nonce 12][ct+tag]`,
   Key = HKDF(X25519(eph, member_x25519), "ms08-sealed-v1"), AAD =
   utf8(group_id). Trägt die `KeyRotation` (`group_secret_{e+1}`, `key_epoch`,
   vollständige Mitgliederliste, Gruppen-Meta) — einheitlich per-Member für
   Join **und** Leave (ein Code-Pfad). Jedes Mitglied hält dafür ein
   gruppen-spezifisches X25519-Keypair (Public-Key in der Mitgliederliste).
7. **Ein OH pro Gruppe pro Mitglied** — die bestehende OH↔Channel-Bindung wird
   wiederverwendet: die Gruppe registriert sich unter `channel_id = group_id`
   (32 B random, hex). **Kein RGB, keine Session-Tags in Gruppen** — jedes
   Mitglied kennt jedes Gruppen-OH aus der Mitgliederliste; Zustellung ist
   immer Forward-Garlic (MS04) mit R-ACK (MS06) je Empfänger.
8. **Join nur über einen bestehenden 1:1-Kanal; QR-Gruppen-Invites sind
   descoped** (Master-AC angepasst). Der Invite ist zwingend ein
   Zwei-Wege-Handshake, weil der Invitee seine Gruppen-Keys selbst erzeugt:
   `InviteProposal` (1:1, neues `ChannelMessage`-Feld 7 `group_handshake`) →
   Invitee generiert member_id/X25519/Gruppen-OH → `JoinAccept {member_id,
   x25519_pub, oh_descriptor}` (1:1) → Admin bumpt Epoche und sendet die
   Sealed Rotation an **alle** inkl. Newcomer (dessen Rotation ist der
   eigentliche Invite mit Mitgliederliste + Meta). Ein QR-Invite müsste das
   Epoch-Secret im Klartext-QR tragen und hätte keine Invitee-Authentisierung.
9. **Genau ein Admin (der Creator) in v1** — Multi-Admin und
   Konflikt-Auflösung (OQ 2/3) sind out of scope; Admin-Verlust ⇒ Gruppe kann
   nur noch lesen/schreiben, nicht mutieren (dokumentierte v1-Grenze).
10. **Epoch-Mismatch = Buffer-and-Drain:** Items einer unbekannten (neueren)
    Epoche werden lokal gepuffert — die Fetch-Quittung löscht sie serverseitig,
    also darf der Client sie nicht verwerfen. Die Rotation kommt über die
    Retry-Queue garantiert an und drainiert den Puffer. Kein
    KeyRequest-Protokoll in v1 (Master-AC angepasst).
11. **Archiv & Bounds (Ratchet-Konstanten gespiegelt):** alte Epochen
    (K_out + Chain-Stände) und Skipped-Keys 30 Tage, max. 512 Skipped-Keys pro
    Sender-Chain / 2048 pro Gruppe. Empfangene Nachrichten liegen entschlüsselt
    in der App-DB — „alte Nachrichten lesbar" braucht keine
    Re-Decrypt-Fähigkeit (OQ 6); das Archiv dient nur nachzüglerischen Items.
12. **Rotation bei jeder Membership-Änderung** (Join: Newcomer liest keine
    Historie; Leave: Entfernter liest nichts Neues). Keine periodische
    Rotation in v1 — der Spike zeigt keinen Nutzen, der n Sealed Boxes pro
    Woche rechtfertigt, solange Chains hash-vorwärts laufen.
13. **Status-Aggregation (MS06-Anschluss):** `sent` = alle n−1 Deposits
    übergeben, `routed` = R-ACK für alle Zustellungen, `delivered` =
    Channel-ACK von allen Mitgliedern (WhatsApp-Semantik: ✓✓ erst wenn alle).
    Channel-ACKs sind normale v5-Nachrichten (`ack_message_id`, Feld 6) —
    jedes Mitglied sieht die ACKs der anderen.

## Open Questions

Answered by the sdd05 spike (see Decisions above):

1. ~~Maximum group size?~~ → 20 (Decision 2).
2. ~~Multiple admins?~~ → one admin (creator) in v1 (Decision 9).
3. ~~Conflicting group state between admins?~~ → moot with a single admin (Decision 9).
4. ~~Sender Keys vs. per-member encryption?~~ → epoch sender keys (Decision 3).
5. ~~"Join via link" flow?~~ → join handshake over an existing 1:1 channel, purely peer-to-peer; QR/link invites descoped (Decision 8).
6. ~~Re-encrypt old messages on rotation?~~ → no; decrypted store + 30-day epoch archive for stragglers (Decision 11).
