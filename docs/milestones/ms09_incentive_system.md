# MS09: Incentive System

## Status: Missing

Only basic HashCash PoW exists in `NodeId.java` (SHA256 leading zero byte). No reputation system, no anti-sybil beyond PoW, no relay incentives.

## Goal

Incentivize full nodes to relay messages honestly and reliably. Score nodes based on observed behavior. Deter Sybil attacks (mass creation of cheap identities to overwhelm the network). Create a self-sustaining network where relay operators are motivated to keep nodes online.

## Prerequisites

- MS06 (Two-Layer ACK) — R-ACK data feeds the reputation system
- MS04 (Multi-Hop Garlic) — relay behavior is what we're scoring

## Current State

| What | Where | Status |
|------|-------|--------|
| HashCash PoW | `NodeId.java` — `SHA256(SHA256(pubkey))[0] == 0` (~1/256 cost) | Done — very low difficulty |
| Node scoring | MS06 `node_scorer.dart` (planned) | Missing |
| Peer stats | `redpanda_light_client.dart` — latency, success/failure counts | Done (local only) |
| Peer performance test | `PeerPerformanceTestSchedulerJob.java` | Done — periodic health checks |

## Spec

### 1. Reputation Scoring

Extend the node scoring from MS06 into a comprehensive reputation system:

```
NodeReputation {
  node_id: KademliaId

  // Relay performance (from R-ACKs, MS06)
  relay_success_count: int       // messages forwarded that resulted in R-ACK
  relay_failure_count: int       // messages forwarded that never got R-ACK
  relay_success_rate: float      // success / (success + failure)
  avg_relay_latency_ms: int

  // OH performance (for nodes hosting OHs)
  oh_uptime_ratio: float         // time online / registered TTL
  oh_fetch_success_rate: float   // successful fetches / total fetch attempts

  // Network contribution
  uptime_hours: int              // total observed uptime
  peer_count: int                // number of peers this node maintains
  dht_entries_hosted: int        // Kademlia entries stored

  // Anti-sybil
  pow_difficulty: int            // difficulty of this node's PoW (number of leading zero bits)
  age_days: int                  // how long this node has been known

  // Composite score
  score: float                   // weighted combination, 0.0 - 1.0
}
```

**Score calculation:**
```
score = w1 * relay_success_rate
      + w2 * normalize(uptime_hours)
      + w3 * normalize(age_days)
      + w4 * normalize(pow_difficulty)
      + w5 * oh_uptime_ratio

Suggested weights: w1=0.35, w2=0.20, w3=0.15, w4=0.15, w5=0.15
```

### 2. Anti-Sybil: Graduated PoW

Replace the fixed 1-byte PoW with a graduated difficulty system:

**`NodeId` PoW tiers:**

| Tier | Difficulty | Leading zero bits | Approx. generation time | Trust bonus |
|------|-----------|-------------------|------------------------|-------------|
| 0 | Minimal | 8 (current) | ~milliseconds | None |
| 1 | Low | 16 | ~seconds | +0.05 score |
| 2 | Medium | 20 | ~minutes | +0.10 score |
| 3 | High | 24 | ~hours | +0.15 score |

- Higher PoW difficulty → more trusted initial score → preferred for hop selection.
- PoW tier is verifiable by any node: `count_leading_zero_bits(SHA256(SHA256(pubkey)))`.
- Existing NodeIds with 8-bit PoW remain valid (tier 0).

### 3. Behavior-Based Penalties

Nodes that behave badly receive score penalties:

| Behavior | Detection | Penalty |
|----------|-----------|---------|
| Dropping messages | No R-ACK for forwarded messages | -0.1 per incident (up to min 0.0) |
| Selective forwarding | Statistical analysis: node forwards some messages but not others | -0.2, flag for avoidance |
| Fake R-ACKs | R-ACK received but Channel-ACK never arrives (correlation over time) | -0.15, requires multiple observations |
| Timestamp manipulation | OH node reports timestamps far from expected | -0.05 |
| DDoS / flooding | Excessive connection attempts or message volume | Temporary ban (1 hour, then exponential) |

### 4. Reputation Gossip

Nodes share reputation observations via the DHT:

```
ReputationReport {
  reporter_node_id: KademliaId
  subject_node_id: KademliaId
  observation_type: enum { RELAY_SUCCESS, RELAY_FAILURE, OH_UPTIME, ... }
  timestamp: int64
  signature: bytes            // Ed25519 signature by reporter
}
```

- Reports are stored in the DHT keyed by `subject_node_id`.
- Each node computes its own composite score by aggregating reports from multiple reporters.
- Reports from higher-reputation reporters carry more weight (transitive trust).
- Self-reports are ignored (a node cannot boost its own score).

### 5. Relay Incentive: Priority Routing

Nodes with higher reputation get:

- **Priority in hop selection**: Senders prefer high-reputation relays → more traffic → more relevance in the network.
- **Priority in OH hosting**: Clients prefer registering OHs on high-reputation nodes → more business.
- **Extended DHT TTL**: High-reputation nodes can keep Kademlia entries for longer (up to 14 days vs 61 minutes for low-rep).

This creates a positive feedback loop: reliable service → higher reputation → more traffic → continued motivation to be reliable.

### 6. Cold Start

New nodes (no history) start with:
- `score = 0.3 + pow_bonus` (neutral-ish, not blocked but not preferred).
- Build reputation by successfully relaying messages over time.
- After 7 days of consistent uptime and >80% relay success rate, score should reach ~0.7.

## Protobuf Changes

```protobuf
message ReputationReport {
  bytes reporter_id = 1;       // 20-byte KademliaId
  bytes subject_id = 2;        // 20-byte KademliaId
  uint32 observation_type = 3; // enum
  int64 timestamp = 4;
  bytes signature = 5;         // Ed25519
  bytes data = 6;              // observation-specific payload
}

message NodeReputationQuery {
  bytes node_id = 1;
}

message NodeReputationResponse {
  bytes node_id = 1;
  float composite_score = 2;
  repeated ReputationReport reports = 3;
}
```

## Backend Changes

| File | Action |
|------|--------|
| `NodeId.java` | Add `getPoWDifficulty()` — count leading zero bits; support graduated tiers |
| **New**: `ReputationService.java` | Aggregate reputation reports, compute composite scores |
| **New**: `ReputationStore.java` | Persist reputation reports (MapDB) |
| `KadStoreManager.java` | Adjust entry TTL based on node reputation |
| `InboundCommandProcessor.java` | Add handlers for reputation query/report commands |
| `PeerPerformanceTestSchedulerJob.java` | Generate reputation reports from observed behavior |
| `Server.java` | Register reputation service, periodic report gossip |

## Mobile Changes

| File | Action |
|------|--------|
| `node_scorer.dart` (from MS06) | Extend with full reputation model, query DHT for reports |
| `hop_selector.dart` (from MS04) | Integrate reputation scores into hop selection |
| **New**: `reputation_screen.dart` | (Optional) Debug screen showing known node reputations |
| `redpanda_light_client.dart` | Publish reputation reports for observed relay behavior |
| `database.dart` | Add `node_reputations` table for caching scores |

## Acceptance Criteria

- [ ] Node reputation score is computed from relay success rate, uptime, PoW difficulty, and age
- [ ] Higher PoW difficulty (more leading zero bits) increases initial trust score
- [ ] Nodes that drop messages see their score decrease over time
- [ ] Hop selection preferentially routes through higher-scored nodes
- [ ] Reputation reports are signed and stored in the DHT
- [ ] Self-reports are rejected (a node cannot boost its own score)
- [ ] New nodes start at score ~0.3 and can reach ~0.7 within 7 days of honest operation
- [ ] A node banned for flooding is temporarily blocked (exponential backoff)
- [ ] Reputation data persists across node restarts

## Open Questions

1. How to prevent reputation poisoning (colluding nodes publishing false reports)?
2. Should reputation be global (aggregated from all reporters) or local (each node computes its own view)?
3. How to handle the cold-start problem for the first nodes in the network (no reporters)?
4. Should there be a monetary incentive (e.g. cryptocurrency) in addition to reputation, or is reputation alone sufficient?
5. How much PoW difficulty is acceptable for mobile clients generating NodeIds? Tier 2 (minutes) may be too slow for first-launch experience.
6. Should reputation decay over time (a node that was reliable 6 months ago but hasn't been seen since)?
