# Frontend MS08: Group Chat

## Status: Done (2026-07-09, mobile [#40](https://github.com/redPanda-project/redpanda-mobile/pull/40))

> **Backend-Abhängigkeit**: Keine — [Backend MS08](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms08_group_chat.md) hat keine Backend-Änderungen
> (bestätigt durch den sdd05-Spike: Modell 3 „Fan-out-Node“ abgelehnt).
> Group Chat ist reine Frontend-Logik. Verbindliche Festlegungen:
> [Decisions (Fan-out-Spike sdd05, 2026-07-08)](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms08_group_chat.md#decisions-fan-out-spike-sdd05-2026-07-08)
> in der Master-Spec — diese View ist daran ausgerichtet.

## Goal

Gruppen-Konversationen mit 3–20 Teilnehmern (Decision 2). Sender-seitiger
Fan-out an jedes Mitglieds-Gruppen-OH (Decision 1/7), Epochen-Sender-Keys mit
Hash-Chain-Forward-Secrecy und Ed25519-Sender-Authentizität (Decision 3/4),
Key Rotation bei jeder Membership-Änderung (Decision 12). Join über einen
bestehenden 1:1-Kanal (Decision 8).

## Prerequisites

- Frontend MS06 Done — R-ACK-Handling + Node-Scoring für Fan-out-Zustellungen
- Frontend MS04 Done — Garlic-Routing (Forward-Pfad je Empfänger)
- Frontend MS03b Done — HKDF/Chain-Muster, Skipped-Key-Konstanten
- Frontend MS02 Done — Retry-Queue (je Empfänger-Zustellung)

RGBs/Session-Tags (MS05) werden in Gruppen **nicht** verwendet (Decision 7).

## Current State

| Component | File | Status |
|-----------|------|--------|
| Group crypto (Epochen-Chains, Envelope v5/v6) | LC `crypto/group_crypto.dart` | Done — inkl. admin-signierter Rotationen (s. Umsetzungsnotizen) |
| Group state + Persistenz | LC `domain/group_state.dart` | Done — `GroupStateUpdate`-Snapshots, Restore via `registerGroup` |
| GroupControl/GroupHandshake-Codecs | LC `crypto/group_control.dart` | Done — proto3-kompatibel |
| Fan-out + v5/v6-Dispatch | LC `client/redpanda_light_client.dart` | Done — `sendGroupMessage()`, Dispatch in `fetchMessages`, R-ACK je Empfänger |
| GroupService | App `services/group_service.dart` | Done — Join-Handshake, Remove + Rotation, Rename, Restore beim Start, Rotation-Retry |
| Group UI | `screens/group/create_group_screen.dart`, `screens/group/group_info_screen.dart`, `screens/chat/chat_screen.dart` | Done — Cap 20, Sender-Namen, Member-Count, Invites im Home-Screen |
| Database | `database.dart` (Drift v14, nicht destruktiv) | Done — `group_channels`, `group_members`, `group_pending_items`, `group_invites`, `message_receipts`, `Messages.senderMemberId` |

## Spec

### 1. Identitäten & Keys (Decision 3/4/6)

Pro Gruppe und Gerät werden frisch generiert (keine Verkettbarkeit zwischen
Gruppen):

- `member_id` = eigener Ed25519-**Verify-Key** (32 B) — Identität = Signierschlüssel.
- Ed25519-Signier-Seed (bleibt auf dem Gerät).
- X25519-Keypair für Sealed Controls (Public-Key steht in der Mitgliederliste).
- Ein eigenes Gruppen-OH, registriert unter `channel_id = group_id`.

Pro Epoche `e` (Admin-generiert, 32 B `group_secret_e`):

```
ck_0(M)  = HKDF(group_secret_e, info = "ms08-chain-v1" ‖ member_id_M)
K_out(e) = HKDF(group_secret_e, info = "ms08-outer-v1")
MK_N     = HKDF(ck_N, "ms08-msg-v1");  ck_{N+1} = HKDF(ck_N, "ms08-adv-v1")
```

Nach Ableitung aller Chains + `K_out` wird `group_secret_e` gelöscht;
verbrauchte `MK_N`/`ck_N` werden gelöscht (Hash-Chain-FS). Skipped-Keys:
max. 512 pro Sender-Chain, 2048 pro Gruppe, 30 Tage (Decision 11).

### 2. Envelope v5 — Gruppennachricht (Decision 5)

Dispatch in `fetchMessages` über das Versions-Byte (wie v3/v4):

```
outer: [0x05][key_epoch 4 BE][nonce 12][ct+tag]          // K_out(e), AAD = utf8(group_id hex)
inner: [member_id 32][N 4 BE][nonce 12][ct+tag][sig 64]  // MK_N
       inner AAD = utf8(group_id) ‖ epoch(4 BE) ‖ member_id ‖ N(4 BE)
       sig = Ed25519(member) über AAD ‖ nonce ‖ ct
       ct  = ChannelMessage (message_id, timestamp, content, ack_message_id …)
```

Fester Overhead 161 B; Content-Budget ≈ 1,33 KiB bei ACKED @ 3+3 Hops.
Kein `reply_path` in Gruppennachrichten (Decision 7).

### 3. Envelope v6 — Sealed Control (Decision 6)

```
[0x06][eph_pub 32][nonce 12][ct+tag]
Key = HKDF(X25519(eph, member_x25519), "ms08-sealed-v1"), AAD = utf8(group_id)
ct  = GroupControl (KeyRotation: secret, epoch, Mitgliederliste, Gruppen-Meta)
```

Einheitlicher Pfad für Join und Leave: der Admin sendet die Rotation als
Sealed Box an jedes Mitglieds-Gruppen-OH (n Deposits über Forward-Garlic).

### 4. Join-Handshake über 1:1-Kanal (Decision 8)

`ChannelMessage` erhält Feld 7 `group_handshake` (bytes, proto3-kompatibel wie
Felder 5/6):

```
GroupHandshake {
  oneof kind {
    InviteProposal proposal = 1;   // group_id, group_name — Admin → Invitee
    JoinAccept     accept   = 2;   // group_id, member_id, x25519_pub, oh_descriptor — Invitee → Admin
  }
}
```

Ablauf: Proposal (1:1, v4-Ratchet) → Invitee generiert Keys + Gruppen-OH →
JoinAccept (1:1) → Admin fügt Mitglied lokal hinzu, bumpt Epoche und sendet die
Sealed Rotation an alle **inklusive** Newcomer (dessen Rotation enthält die
volle Mitgliederliste = der eigentliche Invite). Kein QR-Gruppen-Invite in v1.

### 5. Fan-out-Send (Decision 1/7/13)

`sendGroupMessage(groupId, content)`:

1. Ein v5-Payload bauen (eigene Chain advancen, signieren, outer-verschlüsseln)
   — **ein** Ciphertext für alle Empfänger; Retries senden denselben Payload
   (Dedup über inneres `message_id`, die Chain wird nicht erneut advanced).
2. Für jedes andere Mitglied: Forward-Garlic (MS04) an dessen Gruppen-OH,
   R-ACK angefordert (MS06, eigener Ack-Tag je Zustellung), Retry-Queue-Eintrag
   je (message, member).
3. Statusaggregation: `sent` = alle Deposits übergeben; `routed` = R-ACK für
   alle Zustellungen; `delivered` = Channel-ACK von allen Mitgliedern.
   Channel-ACKs sind normale v5-Nachrichten (`ack_message_id`).

Gruppengröße hart auf 20 begrenzt (Service + UI, Decision 2).

### 6. Key Rotation & Membership (Decision 3/12)

- Trigger: jede Membership-Änderung (kein periodischer Trigger in v1).
- Admin: neues Secret, `key_epoch + 1`, Sealed Rotation an alle Ziel-Mitglieder,
  lokal Chains ableiten, Secret löschen.
- Empfänger: Rotation installieren (Chains + K_out ableiten, Secret löschen),
  Mitgliederliste ersetzen, gepufferte Items der neuen Epoche drainieren.
- Alte Epochen wandern ins Archiv (30 Tage) für nachzüglerische Items.
- Epoch-Mismatch (Item mit unbekannter Epoche): lokal puffern — die
  Fetch-Quittung hat das Item serverseitig gelöscht — und beim Eintreffen der
  Rotation drainieren (Decision 10). Kein KeyRequest in v1.

### 7. Ein Admin (Decision 9)

Nur der Creator mutiert die Gruppe (add/remove/rename/rotate). Nicht-Admins
können senden/empfangen und die Gruppe lokal verlassen.

### 8. Chat-UI

- Sender-Name pro Nachricht (aus Mitgliederliste via `member_id`; Signatur
  bereits in der Krypto-Schicht verifiziert).
- Member-Count im Header, Group-Info-Screen (Liste, Admin-Controls).
- Gruppen-Erstellung: Auswahl aus bestehenden 1:1-Channels mit Peer-OH.
- Status-Icons wie 1:1 (pending→sent→routed→delivered), aggregiert nach
  Decision 13.

### 9. Database Migration (Drift v14, nicht destruktiv)

```
group_channels:
  group_id TEXT PK            -- 32-B hex, zugleich channel_id des Gruppen-OH
  label TEXT
  is_admin BOOLEAN
  my_member_id TEXT           -- eigener Ed25519-Verify-Key (hex)
  my_sign_seed TEXT           -- hex, nur lokal
  my_x25519_priv TEXT         -- hex, nur lokal
  key_epoch INTEGER
  crypto_state TEXT           -- JSON: Chains {member_id: {ck, n, skipped}}, K_out, Epoch-Archiv
                              -- Persistenz-Muster = Channels.ratchetState (Stream-Snapshots)

group_members:
  group_id TEXT + member_id TEXT (PK)
  display_name TEXT
  oh_endpoint TEXT, oh_id TEXT (hex)
  x25519_pub TEXT (hex)
  role INTEGER               -- 0 = admin, 1 = member

group_pending_items:         -- Buffer-and-Drain (Decision 10)
  id INTEGER PK AUTOINCREMENT
  group_id TEXT
  payload BLOB
  received_at_ms INTEGER

messages: + sender_member_id TEXT NULL   -- Sender-Zuordnung in Gruppen
```

## Mobile Changes

| File | Action |
|------|--------|
| **New**: LC `crypto/group_crypto.dart` | Epoch-Install, Chains, Envelope v5/v6 encrypt/decrypt/verify |
| **New**: LC `domain/group_state.dart` | GroupInfo/GroupMember/GroupCryptoState (JSON-Persistenz) |
| **New**: LC `crypto/group_control.dart` | GroupControl/GroupHandshake proto3-kompatible Encoder/Decoder |
| LC `client_facade.dart` + `client/redpanda_light_client.dart` | `addGroupKeys()`, `sendGroupMessage()`, v5/v6-Dispatch in `fetchMessages`, `groupCryptoStateUpdates`/`groupEvents`-Streams |
| LC `client/isolate_protocol.dart` + `isolate_client.dart` | Plumbing für die neuen Calls/Streams |
| LC `crypto/channel_message.dart` | Feld 7 `group_handshake` |
| **New**: App `services/group_service.dart` | Create/Join/Leave, Rotation, Handshake, Statusaggregation |
| **New**: App `screens/group/create_group_screen.dart` | Gruppe anlegen (Auswahl aus 1:1-Channels) |
| **New**: App `screens/group/group_info_screen.dart` | Mitgliederliste, Admin-Controls |
| App `database.dart` | Migration v14 (s. o.) |
| App `chat_screen.dart` | Sender-Namen, Member-Count, Gruppen-Sendepfad |
| App `providers.dart` | `groupServiceProvider`, `groupMembersProvider(groupId)` |

## Acceptance Criteria

- [x] Gruppe mit 3–20 Mitgliedern erstellen (Join-Handshake über 1:1-Kanäle) *(E2E mit 3 Clients; Unit-Tests für den Handshake-Flow)*
- [x] Nachricht an Gruppe → alle Mitglieder empfangen sie (Fan-out an jedes Gruppen-OH) *(ein v5-Ciphertext, n−1 Garlic-Zustellungen; E2E mit Sender-Attribution)*
- [x] Sender-Name wird pro Nachricht angezeigt, Signatur wird verifiziert *(member_id = Ed25519-Verify-Key; Prüfung in `group_crypto.dart`, Forgery-Fälle Unit-getestet)*
- [x] Member hinzufügen → Rotation → Newcomer liest keine Historie *(die Sealed Rotation ist der eigentliche Invite; Chains frisch pro Epoche)*
- [x] Member entfernen → Rotation → Entfernter kann neue Nachrichten nicht lesen *(E2E: entferntes Mitglied kann die Folge-Epoche nicht entschlüsseln)*
- [x] Rename propagiert an alle Mitglieder (GroupControl über v5) *(`GroupInfoUpdate` als regulärer v5-Broadcast, nur Admin)*
- [x] Offline-Mitglieder empfangen Nachrichten beim nächsten Fetch (OH-Mailbox) *(E2E)*
- [x] Epoch-Mismatch: Items werden gepuffert und nach der Rotation drainiert *(bounded, 256 Items pro Gruppe; Unit-getestet)*
- [x] Gruppengröße > 20 wird abgelehnt (Service + UI) *(`maxGroupMembers` in Service, Light Client und UI)*

## Umsetzungsnotizen (Frontend-MS08, 2026-07-09)

Umgesetzt in mobile [#40](https://github.com/redPanda-project/redpanda-mobile/pull/40),
exakt entlang der [Master-Spec-Decisions (sdd05)](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms08_group_chat.md#decisions-fan-out-spike-sdd05-2026-07-08).
Ergänzungen bzw. Präzisierungen gegenüber der Spec:

1. **Admin-signierte Rotationen:** v6-Sealed-Boxen sind zusätzlich Ed25519-signiert
   und werden gegen den beim `InviteProposal` gepinnten Admin verifiziert; eine
   Rotation kann den Admin nicht wechseln (härtet Decision 9 gegen gefälschte
   Rotationen ab).
2. **Drift v14 ergänzt** über das Spec-Schema hinaus `group_invites` (eingehende
   Proposals für den Home-Screen) und `message_receipts` (per-Member-R-ACK-/
   Channel-ACK-Stände für die Aggregation nach Decision 13).
3. **Partial-Failure-Retry:** `GroupSendException.messageIdHex` — Retries senden
   denselben v5-Payload mit derselben `message_id` (die Chain wird beim Retry
   nicht erneut advanced; Dedup bei bereits erreichten Mitgliedern).
4. **Rotation-Zustellung ist Submission-basiert:** unzustellbare Rotation-Boxen
   werden periodisch erneut versucht (Timer + Retry beim App-Start) — kein
   eigenes Bestätigungsprotokoll in v1.
5. **Tests:** 17 neue LC-Unit-Tests (Krypto-Roundtrips, Forgery/Replay/Removal,
   Codecs, JSON-Persistenz), App-Tests für Handshake-Flow, Receipt-Aggregation
   und Migration v14; E2E `ms08_group_chat_test.dart` gegen 4 echte Nodes mit
   3 Clients. CI-E2E-Step-Timeout 20 → 30 min (längerer serieller Lauf).

## Open Questions

Alle v1-Fragen sind durch die Master-Spec-Decisions beantwortet (sdd05).
Explizit auf später verschoben: QR-/Link-Invites, Multi-Admin, Fan-out-Node
für n > 20, periodische Rotation, Sende-Jitter gegen Burst-Analyse am
Submit-Node.
