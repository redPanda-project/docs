# MS05: Reverse Garlic

## Status: Missing

ARC42 (`04_solution_strategy.adoc`, `06_runtime_view.adoc`, `08_concepts.adoc`) specifies Reverse Garlic Blocks (RGBs) as the mechanism for reply paths. No code exists.

## Spike Required Before MS04 Implementation

> **This spike must be completed before MS04 is implemented, not after.** Its outcome decides whether the RGB concept fits in a packet at all, and freezing the RGB format prematurely risks re-learning attacks that the prior art already documents.

Two questions must be answered first:

1. **Size budget.** Compute whether a 3-hop RGB plus padding fits inside the Flaschenpost v2 budget. Per MS04, a 2048-byte Flaschenpost v2 packet leaves **1963 payload bytes**. Each onion layer adds a destination (20-byte KademliaId), an ephemeral public key, an AEAD nonce + tag, and padding; three nested layers plus the `ChannelMessage` content compete for those 1963 bytes. The spike must produce a concrete byte accounting and confirm a 3-hop RGB + a usable `content` fits — or determine the maximum hop count / content size that does, **before** the wire format and `ReverseGarlicBlock` layout are frozen.

2. **Prior-art review.** Reverse Garlic Blocks re-invent **I2P SURBs** (Single-Use Reply Blocks) and **Sphinx reply blocks**. These have documented attack classes that the current MS05 Open Questions re-ask from scratch (single-use vs. reusable, batch size, size vs. packet budget). The spike must review and explicitly address their known attack classes before the RGB format is frozen:
   - **Tagging attacks** on reply blocks (a malicious hop marks a packet to correlate it downstream).
   - **Replay** of a reply block (re-using a captured RGB to flood / correlate).
   - **Size correlation** (RGB or padding size leaks path length or links forward and reply paths).

   The MS05 Open Questions below (single-use? batch size? size vs. 2048-byte limit?) are largely answered in the SURB/Sphinx literature — answer them from that literature rather than re-deriving them and paying the tuition twice.

**Deliverable of the spike:** a short byte-budget table (per hop, per layer) confirming feasibility, and a one-page mapping of each Sphinx/SURB attack class to the chosen RGB defense (or an explicit decision to accept the risk). MS05 implementation does not start until both are signed off.

## Goal

Enable Bob to send a reply to Alice without knowing Alice's network location or OH node. Alice pre-builds an encrypted reply path (RGB) and includes it in her outgoing message. Bob uses the RGB to route his reply back through Alice's chosen hops to Alice's OH.

## Prerequisites

- MS04 (Multi-Hop Garlic) — forward-path garlic routing working

## Current State

| What | Where | Status |
|------|-------|--------|
| RGB data model | ARC42 `08_concepts.adoc` | Spec only |
| Reply block concept | ARC42 `06_runtime_view.adoc` | Runtime diagram only |
| Session tags | — | Missing |
| Garlic forward path | `GarlicMessage.java` / MS04 | Partial → Done after MS04 |

## Spec

### 1. RGB Data Model

```
ReverseGarlicBlock {
  version: uint8           // 1
  expiry_ts: int64          // Unix ms — when this RGB expires
  session_tag: bytes[16]    // Random tag Alice uses to identify which channel/conversation this reply belongs to
  first_hop: KademliaId    // The first relay in the return path (H1)
  encrypted_layers: bytes   // Pre-encrypted onion layers (H1 → H2 → H3 → Alice's OH)
}
```

### 2. RGB Construction (Alice, Sender of Original Message)

Alice builds an RGB that Bob can use to reply:

```
Return path: Bob → H1' → H2' → H3' → Alice's OH

Alice picks 3 return-path hops (H1', H2', H3') — different from forward-path hops.

Layer 3 (innermost, for H3'):
  plaintext_3 = [CMD_DELIVER][20 Alice_OH_kademlia_id][16 session_tag][padding]
  layer_3 = encrypt(H3'.enc_pub, plaintext_3)

Layer 2 (for H2'):
  plaintext_2 = [CMD_FORWARD][20 H3'_kademlia_id][layer_3 + padding]
  layer_2 = encrypt(H2'.enc_pub, plaintext_2)

Layer 1 (outermost, for H1'):
  plaintext_1 = [CMD_FORWARD][20 H2'_kademlia_id][layer_2 + padding]
  layer_1 = encrypt(H1'.enc_pub, plaintext_1)

RGB.encrypted_layers = layer_1
RGB.first_hop = H1'_kademlia_id
```

### 3. RGB Inclusion in Outgoing Messages

When Alice sends a message to Bob, the channel-encrypted payload includes:

```
ChannelMessage {
  message_id: bytes
  content: bytes            // actual message text
  rgb: ReverseGarlicBlock   // pre-built reply path
  timestamp: int64
}
```

Alice attaches a fresh RGB to every message (or every N messages). Each RGB is single-use — once Bob uses it, Alice must provide a new one.

### 4. Using an RGB (Bob Replies)

When Bob wants to reply to Alice:

1. Take the most recent unused RGB from Alice.
2. Encrypt the reply with `K_enc` (channel encryption key).
3. Build a Flaschenpost v2 packet:
   - `next_hop` = `RGB.first_hop`
   - `encrypted_payload` = `[Bob's encrypted reply][RGB.encrypted_layers]`
4. Send the packet to `RGB.first_hop` (via Bob's own garlic forward path or directly if connected).

Each relay along the return path peels its layer (same as MS04 forward path), eventually delivering to Alice's OH with the `session_tag` attached.

### 5. Session Tags

Session tags allow Alice to correlate incoming replies with conversations:

- Alice generates a random 16-byte `session_tag` per RGB.
- Alice stores `session_tag → channel_id` in a local lookup table (Drift).
- When Alice fetches a message from her OH and it contains a `session_tag`, she looks up the corresponding channel and decrypts with that channel's `K_enc`.
- Session tags are single-use: after receiving a reply with a given tag, Alice removes it from the lookup.

### 6. RGB Expiry and Rotation

- Each RGB has an `expiry_ts` (e.g. 24 hours from creation).
- If Bob hasn't replied before expiry, the RGB is useless (the return-path hops may have rotated keys).
- Alice should include a fresh RGB in every message to ensure Bob always has a valid return path.
- If Alice changes her OH (e.g. moves to a different full node), old RGBs pointing to the old OH become invalid.

### 7. Privacy Properties

- Bob never learns Alice's OH node or network location — he only sees `RGB.first_hop`.
- Each relay on the return path only sees the next hop.
- The session tag is opaque to relays (it's inside the innermost encrypted layer).
- Different RGBs use different hops, preventing long-term path correlation.

## Protobuf Changes

```protobuf
// New message in commands.proto or a new rgb.proto:
message ReverseGarlicBlock {
  uint32 version = 1;
  int64 expiry_ts = 2;
  bytes session_tag = 3;       // 16 bytes
  bytes first_hop = 4;         // 20-byte KademliaId
  bytes encrypted_layers = 5;  // Pre-encrypted onion layers
}

// Extend ChannelMessage (from MS01):
message ChannelMessage {
  bytes message_id = 1;
  bytes content = 2;
  bytes iv = 3;
  int64 timestamp = 4;
  ReverseGarlicBlock reply_path = 5;  // NEW
}
```

## Backend Changes

| File | Action |
|------|--------|
| `GarlicRouter.java` (from MS04) | Handle `CMD_DELIVER` with session_tag: include tag in mailbox deposit |
| `OutboundMailboxStore.java` | Store session_tag alongside `MailItem` payload |
| No other backend changes | Relays are stateless — they just peel and forward, same as MS04 |

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `rgb_builder.dart` | Construct ReverseGarlicBlocks (pick hops, build layers) |
| **New**: `session_tag_store.dart` | Map session_tag → channel_id, backed by Drift |
| `garlic_builder.dart` (from MS04) | Include RGB in outgoing `ChannelMessage` |
| `redpanda_light_client.dart` | On fetch: extract session_tag, look up channel, decrypt |
| `database.dart` | Add `session_tags` table: `tag BLOB PK`, `channel_id TEXT`, `created_at DATETIME` |
| `channel.dart` | Add method to generate RGB for this channel |

## Acceptance Criteria

- [ ] Alice can build an RGB and include it in an outgoing message
- [ ] Bob can use the RGB to send a reply without knowing Alice's OH node
- [ ] The reply traverses 3 hops and arrives at Alice's OH
- [ ] Alice correlates the reply to the correct channel via session_tag
- [ ] Each RGB is single-use — reusing it fails or is detected
- [ ] Expired RGBs are rejected (message dropped, not delivered)
- [ ] No relay on the return path can determine both Bob's identity and Alice's OH
- [ ] A two-way conversation works: Alice sends with RGB, Bob replies via RGB, Alice sends again with a new RGB, etc.

## Open Questions

1. Should RGBs be single-use (maximum privacy) or reusable within a session (simpler)?
2. How many RGBs should Alice pre-generate and send to Bob? One per message, or a batch?
3. What happens if all of Bob's RGBs for Alice expire? Is there a fallback (e.g. Bob sends to Alice's OH directly if he knows it from channel setup)?
4. Should the RGB include a reply encryption key (so Bob's reply is encrypted specifically for Alice), or rely on channel `K_enc`?
5. How large can an RGB be before it makes the ChannelMessage too big for a single 2048-byte Flaschenpost?
