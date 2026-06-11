# MS02b: OH Discovery & Forwarding

## Status: Partial — Backend Done (2026-06-11), Frontend-Anteil offen

> Backend umgesetzt in redpandaj [#217](https://github.com/redPanda-project/redpandaj/pull/217) (Deposit-Härtung), [#218](https://github.com/redPanda-project/redpandaj/pull/218) (OH→Node-Discovery), [#219](https://github.com/redPanda-project/redpandaj/pull/219) (Forwarding Option A). Die getroffenen Entscheidungen stehen im Abschnitt [Decisions](#decisions-backend-2026-06-11). Der kleine Frontend-Anteil (Status-Codes im Send-/Retry-Pfad, `want_response`) ist weiterhin offen — siehe [Frontend MS02b](https://github.com/redPanda-project/docs/blob/main/docs/milestones/frontend/ms02b_oh_discovery_forwarding.md).

Message delivery currently only works when the sender is directly connected to the recipient's OH host node. When a `FlaschenpostPut` is forwarded between full nodes, the `oh_id` field is lost (`GMParser.sendFpToPeer()` rebuilds the packet with `content` only), so a forwarded packet can only be delivered via the legacy garlic-header fallback. There is also no discovery mechanism that maps an `oh_id` to its host node, and the unauthenticated deposit path allows a mailbox-flush attack. This milestone closes the largest planning gap between the demo case and real decentralized routing.

## Goal

Make OH delivery work when the sender is **not** directly connected to the recipient's OH node, and harden the deposit path against trivial abuse. After this milestone, Alice can deposit into Bob's mailbox by resolving Bob's OH to its host node and forwarding the packet across the network without losing the `oh_id`.

## Prerequisites

- MS02 (Reliable Delivery) — sequence-based mailbox, delete-after-acknowledge, overflow flag

## Current State

> Historischer Stand **vor** der Backend-Umsetzung (2026-06-11) — die Lücken in dieser Tabelle sind im Backend geschlossen, siehe [Decisions](#decisions-backend-2026-06-11).

| What | Where | Status |
|------|-------|--------|
| Deposit on local OH | `InboundCommandProcessor.handleFlaschenpostPut()` | Done — only if `oh_id` registered locally |
| Forwarding preserves `oh_id` | `GMParser.sendFpToPeer()` | Missing — rebuilds with `content` only, `oh_id` dropped |
| Garlic-header fallback | `tryDepositToLocalOh()` | Done — extracts 20-byte garlic destination as `oh_id` (legacy) |
| OH → host-node discovery | — | Missing — endpoint known only out-of-band via channel QR |
| Deposit authorization | `OutboundService.depositMessage()` | Missing — checks handle existence/expiry only |
| Eviction strategy | `OutboundMailboxStore` | Done — drop-oldest (FIFO), enables silent flush attack |
| Per-item size limit / byte quota | — | Missing — 500-item cap counts items, not bytes |
| Register rate limit | — | Missing — handle-store exhaustion possible |
| Status codes `RATE_LIMIT`/`QUOTA_EXCEEDED`/`BAD_REQUEST` | `outbound.proto` | Defined but never sent |
| `oh_id` / KademliaId domain separation | — | Missing — shared 20-byte namespace, shadowing risk |

## Spec

### 1. Preserve `oh_id` When Forwarding

The forwarding path must carry the `oh_id` so the destination node can deposit into the correct mailbox. Choose **one** of the following, and document the decision explicitly:

**Option A — preserve `oh_id` (recommended):**
- Extend `GMParser.sendFpToPeer()` (and the `FlaschenpostPut` builder it uses) to take and set the `oh_id` field alongside `content`.
- The receiving node's `handleFlaschenpostPut()` then deposits via the explicit `oh_id` path, exactly as for a directly connected sender.

**Option B — delivery via garlic destination only:**
- Decide that cross-node delivery routes **exclusively** through the garlic destination (the garlic-header fallback), and restrict the explicit `oh_id` deposit path to **direct light-client connections** only.
- Document that an explicit `oh_id` from a peer (not a light client) is rejected with `BAD_REQUEST`, so the two paths cannot be confused.

Option A keeps the `oh_id` model uniform across hops and is the smaller change; Option B is cleaner but couples OH delivery tightly to the garlic layer. The recommendation is Option A.

### 2. OH → Node Resolution (Discovery)

Two complementary mechanisms:

**Short-term — endpoint in the channel QR (exists today):**
- The channel QR (`OHDescriptor`) already carries the OH host node endpoint. The sender routes directly to that endpoint. This works for the pairing case but is static (breaks if the recipient migrates their OH).

**Medium-term — DHT announce `H(oh_id) → NodeInfo`:**
- The OH host node announces a DHT record keyed by `H(oh_id)` whose value is its `NodeInfo` (endpoint, node id).
- The sender resolves `H(oh_id) → NodeInfo` before forwarding when it has no direct connection and the QR endpoint is stale.
- **Anti-profiling:** naive DHT lookups leak the sender's interest in a specific `oh_id`. The announce/lookup must use padding and randomized delay so that lookup timing and size do not reveal which OH is being resolved. (This is the same concern flagged for the OHDescriptor lookup in `11_risks_and_technical_debt.adoc`.)

### 3. Deposit Hardening

Address the unauthenticated-deposit / mailbox-flush attack:

- **Reject-new eviction instead of drop-oldest:** When the mailbox is full, reject the **new** deposit (return `QUOTA_EXCEEDED`) rather than evicting the oldest item. Spam can then only block a full mailbox, never silently displace already-stored real messages.
- **Per-`MailItem` size limit:** Reject deposits whose payload exceeds a fixed byte limit (return `BAD_REQUEST`). Today the 500-item cap counts items, not bytes — a single item may be arbitrarily large.
- **Byte quota per mailbox:** Track total bytes per mailbox and reject deposits that would exceed the quota (return `QUOTA_EXCEEDED`), independent of the item count.
- **Register rate limit per connection:** Limit `RegisterOhRequest` frequency per connection to prevent handle-store exhaustion (return `RATE_LIMIT`). 

The proto status codes `RATE_LIMIT`, `QUOTA_EXCEEDED`, and `BAD_REQUEST` already exist in `outbound.proto` and are currently never used — this milestone wires them in.

> Note: full deposit *authorization* (e.g. a pre-shared capability/deposit token so only contacts can deposit) is deferred to a later milestone (originally an MS09 idea). MS02b does the cheap, high-value hardening: stop silent displacement and trivial exhaustion.

### 4. `oh_id` Domain Separation

`tryDepositToLocalOh()` extracts a 20-byte garlic **destination** (a node KademliaId) and treats it directly as an `oh_id`. OH ids and node ids therefore share one 20-byte namespace with no domain separation, allowing a registered OH to shadow a node id. Choose one:

- **Domain-separate `oh_id`:** define `oh_id = H(domain_tag || pubkey)` (distinct from `KademliaId = H(pubkey)`), and enforce the derivation/length at `RegisterOh`.
- **Scheduled removal of the legacy fallback:** the garlic-header fallback exists only because the `oh_id` field was added later; once the frontend always sends an explicit `oh_id` (post Frontend-MS01), schedule removal of `tryDepositToLocalOh()` and the shared-namespace behavior.

## Protobuf Changes

```protobuf
// outbound.proto — Status already defines the codes used here:
//   enum Status { OK = 0; ... RATE_LIMIT = ...; QUOTA_EXCEEDED = ...; BAD_REQUEST = ...; }
// No new fields strictly required for forwarding if Option A reuses FlaschenpostPut.oh_id.

// If a DHT announce record is formalized:
message OhNodeRecord {
  bytes oh_id_hash = 1;   // H(oh_id)
  bytes node_id = 2;      // 20-byte KademliaId of the host node
  string endpoint = 3;    // host:port (may be omitted in favor of node lookup)
  int64 announced_at_ms = 4;
}
```

## Backend Changes

| File | Action |
|------|--------|
| `GMParser.java` | `sendFpToPeer()`: carry and set `oh_id` on the forwarded `FlaschenpostPut` (Option A) |
| `InboundCommandProcessor.java` | On forwarded packet with `oh_id`, deposit via explicit path; for Option B, restrict explicit `oh_id` to direct light-client connections |
| `OutboundMailboxStore.java` | Reject-new eviction; per-item size limit; per-mailbox byte quota |
| `OutboundService.java` | Return `QUOTA_EXCEEDED` / `BAD_REQUEST`; register rate limit per connection → `RATE_LIMIT` |
| `OutboundHandleStore.java` | Enforce `oh_id` derivation/length on register (domain separation) |
| DHT / `KadStoreManager.java` | Announce `H(oh_id) → NodeInfo`; lookup with padding/delay |

## Mobile Changes

| File | Action |
|------|--------|
| `redpanda_light_client.dart` | Resolve `H(oh_id) → NodeInfo` via DHT when no direct connection / stale QR endpoint; handle `QUOTA_EXCEEDED`/`RATE_LIMIT`/`BAD_REQUEST` deposit responses |
| `channel.dart` | If `oh_id` derivation changes (domain separation), update OHDescriptor generation |

## Decisions (Backend, 2026-06-11)

1. **Option A gewählt — `oh_id` bleibt beim Forwarding erhalten.** `GMParser.sendFpToPeer()` trägt `oh_id` (und `hop_count`, neues Feld 4, Limit 3 Hops als Loop-Schutz) auf der weitergeleiteten `FlaschenpostPut`. Der explizite `oh_id`-Pfad ist **authoritativ**: Pakete mit `oh_id` werden deposited, abgelehnt oder geforwardet — sie fallen nie mehr in das Legacy-Garlic-Parsing (das rohe Client-Payloads als GarlicMessage fehlinterpretierte und Light-Client-Verbindungen abreißen konnte).
2. **DHT-Announce über abgeleitete Keys, Record speichert nur die Node-Id** (Open Question 2): Die DHT speichert nur selbstzertifizierende Records (`Key = H(dateUTC‖pubkey)`), daher wird das Announce-Keypair deterministisch aus der oh_id abgeleitet (`seed = SHA256("redpanda.oh.announce.v1" ‖ oh_id)` → brainpoolp256r1, Klasse `OhDht`). Jeder, der die oh_id kennt — und nur der — kann Lookup-Key und Signatur prüfen; null Wire-Änderungen. Der `OhNodeRecord` enthält nur die 20-Byte-Host-Node-Id (kein Endpoint) — Endpoints laufen über den regulären NodeInfo-Lookup. **Trade-off (dokumentiert):** Wer die oh_id kennt (= Deposit-Capability), kann den Record auch überschreiben (newest-wins); authentifizierte Announces (Bindung an den oh_auth-Key) sind auf später verschoben.
3. **Anti-Profiling-Parameter** (Open Question 3): Records fix auf 256 Bytes gepadded; Announces pro OH zufällig gestaggert (0–30 s), Re-Announce-Periode 30 ± 5 min (nötig wegen täglicher Key-Rotation); Resolve-Lookups starten nach 0–1,5 s Zufallsdelay, lokale Cache-Treffer sofort (lokaler Read leakt nichts).
4. **Reject-new gewählt** (Open Question 4): Volle Mailbox (Item-Cap 500 **oder** Byte-Quota 4 MiB) lehnt das neue Deposit ab (`QUOTA_EXCEEDED`); Per-Item-Limit 64 KiB (`BAD_REQUEST`); Register-Rate-Limit 5/min pro Verbindung (`RATE_LIMIT`, vor der Signaturprüfung). Das Overflow-Flag in der FetchResponse bedeutet jetzt „Deposits wurden seit dem letzten Fetch abgelehnt“. Bekannter Rest-Vektor: Ein Angreifer mit bekannter oh_id kann eine Mailbox vollhalten (Blocking, kein Verdrängen mehr) — ein Deposit-Rate-Limit/Deposit-Token bleibt für ein späteres Milestone (MS09-Idee).
5. **Domänentrennung: geplante Abkündigung des Legacy-Fallbacks** (Open Question 5): `tryDepositToLocalOh()` (Garlic-Header-Destination als oh_id) ist im Code als *scheduled for removal* markiert; das Frontend sendet seit MS01 immer ein explizites `oh_id`. Sobald kein Legacy-Traffic mehr existiert, wird der Fallback und damit der geteilte Namespace entfernt — keine zweite Migration nötig, da neue Pfade ihn nie nutzen. Der Announce-Namespace ist per Domain-Tag bereits von Node-Ids getrennt.
6. **Neu (für Frontend-MS02b):** Opt-in-Deposit-Response — `FlaschenpostPut.want_response` (Feld 3) + `FlaschenpostPutResponse` (Command 158), nur an direkt verbundene Light Clients, die das Feld setzen (Bestandsclients desyncen bei unbekannten Commands). Damit sind `RATE_LIMIT`/`QUOTA_EXCEEDED`/`BAD_REQUEST` erstmals tatsächlich auf dem Draht; `OK` bedeutet „deposited **oder** zum Forwarding angenommen (best-effort)“.

## Acceptance Criteria

- [x] A `FlaschenpostPut` forwarded across at least one intermediate node still carries `oh_id` and is deposited into the correct mailbox (Option A; Akzeptanztest `OhForwarderTest`)
- [x] A sender not directly connected to the recipient's OH node can resolve the host node (QR endpoint short-term; derived-key DHT record medium-term) and deliver
- [x] DHT OH lookups use padding and randomized delay (lookup timing/size does not reveal the queried `oh_id`)
- [x] A full mailbox rejects new deposits (`QUOTA_EXCEEDED`) instead of dropping the oldest item — existing messages are never silently displaced
- [x] Deposits exceeding the per-item size limit are rejected with `BAD_REQUEST`
- [x] A per-mailbox byte quota is enforced independent of item count
- [x] Excessive `RegisterOhRequest` from one connection is rejected with `RATE_LIMIT`
- [x] `oh_id` and node KademliaId no longer share an undifferentiated namespace (legacy garlic-header fallback is scheduled for removal and documented; announce namespace domain-separated)
- [ ] Frontend: `RATE_LIMIT`/`QUOTA_EXCEEDED`/`BAD_REQUEST` im Send-/Retry-Pfad ausgewertet, `want_response` gesetzt (Frontend-MS02b)

## Open Questions

Alle fünf ursprünglichen Open Questions sind durch die [Decisions](#decisions-backend-2026-06-11) beantwortet. Offen bleibt nur:

1. Authentifizierte Announces (Record an den registrierten `oh_auth`-Key binden), damit Kenntnis der oh_id nicht zum Überschreiben des Announce-Records reicht — verschoben, zusammen mit Deposit-Autorisierung (MS09-Idee).
2. Deposit-Rate-Limit gegen das Vollhalten fremder Mailboxen (Rest-Vektor von reject-new) — verschoben.
