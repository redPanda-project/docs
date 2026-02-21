# MS04: Multi-Hop Garlic Routing

## Status: Partial

`GarlicMessage.java` implements single-layer encryption (sender → destination). No multi-hop layer peeling, no padding, no relay forwarding logic beyond flood/DHT routing.

## Goal

Route messages through 3 intermediate full nodes (hops), where each hop can only decrypt its own layer to learn the next hop. No single node knows both sender and recipient. Messages are padded to a fixed size to prevent traffic analysis.

## Prerequisites

- MS03 (Authenticated Encryption) — Ed25519/X25519 + AES-256-GCM in place

## Current State

| What | Where | Status |
|------|-------|--------|
| Single-layer garlic encryption | `GarlicMessage.java` | Done — AES-CTR + ECDH (to be upgraded in MS03) |
| `Flaschenpost` base class | `Flaschenpost.java` | Done — destination KademliaId, dedup ID, timestamp |
| GMType enum | `GarlicMessage.java` | Done — GARLIC_MESSAGE(1), FLASCHEN_POST(2), etc. |
| GM dedup cache | `GMStoreManager.java` | Done — 5-minute seen-message window |
| Kademlia routing | `KadStoreManager.java`, peer routing tables | Done — can find nodes by KademliaId |
| Mobile garlic wrapper | `garlic_message_wrapper.dart` | Exists — not integrated |

## Spec

### 1. Packet Format (Flaschenpost v2)

Each garlic packet has a fixed size of **2048 bytes** (configurable constant):

```
Flaschenpost v2:
  [1 version = 0x02]
  [4 packet_id]           // random, for dedup
  [20 next_hop]           // KademliaId of next relay (or final destination)
  [12 nonce]              // AES-256-GCM nonce
  [32 ephemeral_pub]      // X25519 ephemeral public key for this layer
  [N  encrypted_payload]  // AES-256-GCM ciphertext
  [16 auth_tag]           // GCM authentication tag
  ─────────────────────
  Total = 2048 bytes (payload size = 2048 - 85 = 1963 bytes)
```

### 2. Layer Construction (Sender)

Alice builds a 3-hop garlic message to reach Bob's OH node:

```
Path: Alice → H1 → H2 → H3 → OH_node

Layer 3 (innermost, for H3):
  plaintext_3 = [1 CMD_DELIVER][20 OH_kademlia_id][remaining: actual_message + padding]
  layer_3 = encrypt(H3.enc_pub, plaintext_3)

Layer 2 (for H2):
  plaintext_2 = [1 CMD_FORWARD][20 H3_kademlia_id][remaining: layer_3 + padding]
  layer_2 = encrypt(H2.enc_pub, plaintext_2)

Layer 1 (outermost, for H1):
  plaintext_1 = [1 CMD_FORWARD][20 H2_kademlia_id][remaining: layer_2 + padding]
  layer_1 = encrypt(H1.enc_pub, plaintext_1)
```

Each layer's plaintext starts with a command byte:
- `CMD_FORWARD (0x01)`: Peel this layer, forward the inner packet to `next_hop`.
- `CMD_DELIVER (0x02)`: This is the final hop; deliver the payload to the local OH service.

### 3. Layer Peeling (Relay)

When a full node receives a Flaschenpost v2:

1. Check `packet_id` in dedup cache; drop if seen.
2. Try to decrypt `encrypted_payload` using own X25519 private key + `ephemeral_pub`.
3. If decryption fails → this packet is not for us; ignore (do NOT flood-forward).
4. If decryption succeeds:
   - Read command byte.
   - `CMD_FORWARD`: Extract `next_hop` KademliaId and inner packet. Re-pad to 2048 bytes. Forward to `next_hop` via Kademlia routing.
   - `CMD_DELIVER`: Extract payload. Pass to `OutboundService` for mailbox deposit.

### 4. Hop Selection

**Sender (mobile client) picks 3 hops:**

1. Query the local peer table for known full nodes.
2. Select 3 nodes that are:
   - Online (recently seen).
   - Not the sender's own connected node (avoid trivial correlation).
   - Not the destination OH node.
   - Geographically / topologically diverse (if metadata available).
3. For each hop, the sender needs the node's X25519 encryption public key (available via the Kademlia DHT or peer exchange).

**Hop key discovery:**
- During peer list exchange (`SendPeerList`), include the encryption public key in `PeerInfoProto`.
- Alternatively, query the DHT for a node's current public key.

### 5. Padding

- Every Flaschenpost v2 packet is exactly 2048 bytes.
- After constructing the inner plaintext, pad with random bytes to fill the remaining space.
- This prevents an observer from determining the message length or the number of remaining hops.

### 6. Dummy Traffic (Optional, Deferred)

For stronger anonymity, nodes can periodically send dummy Flaschenpost packets (encrypted random bytes to random KademliaIds). This is deferred to a later milestone but the packet format should accommodate it.

## Protobuf Changes

**`commands.proto`:**
```protobuf
// Add encryption public key to peer info
message PeerInfoProto {
  string ip = 1;
  int32 port = 2;
  NodeIdProto node_id = 3;
  bytes encryption_public_key = 4;  // NEW: 32-byte X25519 public key
}
```

No other proto changes — the Flaschenpost v2 format is binary, not protobuf.

## Backend Changes

| File | Action |
|------|--------|
| **New**: `FlaschenpostV2.java` | Parse/serialize v2 packets, fixed 2048-byte size |
| **New**: `GarlicRouter.java` | Layer peeling logic: decrypt → CMD_FORWARD/CMD_DELIVER dispatch |
| `GarlicMessage.java` | Keep for v1 backward compat during transition; deprecate |
| `InboundCommandProcessor.java` | Add handler for `FLASCHENPOST_V2` command type |
| `ConnectionHandler.java` | Forward v2 packets to Kademlia-routed next hop |
| `GMStoreManager.java` | Extend dedup cache to v2 packet IDs |
| `PeerList` handling | Include X25519 encryption public key in peer exchange |

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `garlic_builder.dart` | Build 3-layer garlic packets (layer construction) |
| **New**: `hop_selector.dart` | Select 3 relay nodes from known peers |
| `redpanda_light_client.dart` | Use `garlic_builder` in `sendMessage()` instead of single-layer |
| `garlic_message_wrapper.dart` | Update for v2 format |
| `peer_repository.dart` | Store encryption public keys for known peers |

## Acceptance Criteria

- [ ] A message traverses exactly 3 intermediate hops before reaching the OH node
- [ ] Each relay can only see the next hop, not the final destination or the sender
- [ ] All Flaschenpost v2 packets are exactly 2048 bytes regardless of payload size
- [ ] A relay that is not on the path cannot decrypt the packet (decryption fails gracefully)
- [ ] Dedup prevents the same packet from being forwarded twice by any node
- [ ] Hop selection avoids the sender's direct node and the destination OH node
- [ ] Messages still arrive reliably (MS02 retry logic works with multi-hop)

## Open Questions

1. Fixed packet size: 2048 bytes enough? Larger messages need fragmentation — defer to a later milestone?
2. How many hops? 3 is standard for onion routing, but adds latency. Should it be configurable?
3. Should the sender include a return path (RGB) in the garlic layers, or is that strictly MS05?
4. How to handle the case where a selected hop node is offline? Retry with different hops, or fall back to fewer hops?
5. Should dummy traffic be part of this milestone or deferred?
