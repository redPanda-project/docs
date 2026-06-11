# MS02b: OH Discovery & Forwarding

## Status: Missing

Message delivery currently only works when the sender is directly connected to the recipient's OH host node. When a `FlaschenpostPut` is forwarded between full nodes, the `oh_id` field is lost (`GMParser.sendFpToPeer()` rebuilds the packet with `content` only), so a forwarded packet can only be delivered via the legacy garlic-header fallback. There is also no discovery mechanism that maps an `oh_id` to its host node, and the unauthenticated deposit path allows a mailbox-flush attack. This milestone closes the largest planning gap between the demo case and real decentralized routing.

## Goal

Make OH delivery work when the sender is **not** directly connected to the recipient's OH node, and harden the deposit path against trivial abuse. After this milestone, Alice can deposit into Bob's mailbox by resolving Bob's OH to its host node and forwarding the packet across the network without losing the `oh_id`.

## Prerequisites

- MS02 (Reliable Delivery) — sequence-based mailbox, delete-after-acknowledge, overflow flag

## Current State

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

## Acceptance Criteria

- [ ] A `FlaschenpostPut` forwarded across at least one intermediate node still carries `oh_id` and is deposited into the correct mailbox (or Option B is documented and the explicit `oh_id` path is restricted to direct light-client connections)
- [ ] A sender not directly connected to the recipient's OH node can resolve the host node (QR endpoint short-term; `H(oh_id) → NodeInfo` DHT record medium-term) and deliver
- [ ] DHT OH lookups use padding and randomized delay (lookup timing/size does not reveal the queried `oh_id`)
- [ ] A full mailbox rejects new deposits (`QUOTA_EXCEEDED`) instead of dropping the oldest item — existing messages are never silently displaced
- [ ] Deposits exceeding the per-item size limit are rejected with `BAD_REQUEST`
- [ ] A per-mailbox byte quota is enforced independent of item count
- [ ] Excessive `RegisterOhRequest` from one connection is rejected with `RATE_LIMIT`
- [ ] `oh_id` and node KademliaId no longer share an undifferentiated namespace (domain separation enforced on register, **or** the legacy garlic-header fallback is scheduled for removal and documented)

## Open Questions

1. Option A (preserve `oh_id`) vs Option B (garlic-destination-only) — which becomes the canonical cross-node delivery path?
2. Should the DHT announce store the endpoint directly, or only the node id (resolved via a second Kademlia lookup) to reduce what a single record reveals?
3. What padding/delay parameters defeat lookup profiling without making resolution unusably slow on mobile?
4. Reject-new vs drop-oldest: does reject-new create a denial-of-service vector (attacker keeps a mailbox full)? Does this need rate-limited deposit on top?
5. Domain separation now (`H(domain_tag || pubkey)`) vs. deferring until the legacy fallback is removed — which avoids a second migration?
