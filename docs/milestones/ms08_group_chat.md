# MS08: Group Chat

## Status: Missing

ARC42 hints at multi-OH fan-out. No group chat code exists. The current channel model is 1-to-1 only.

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

- [ ] A group can be created with 3+ members
- [ ] All members receive messages sent to the group
- [ ] Adding a member triggers key rotation; old key still decrypts old messages
- [ ] Removing a member triggers key rotation; removed member cannot read new messages
- [ ] Group admin can rename the group; change propagates to all members
- [ ] Fan-out sends messages to each member's OH independently
- [ ] Messages display the sender's name in the group chat UI
- [ ] Group invite can be shared via QR code (encrypted for the invitee)
- [ ] Offline members receive messages when they come back online (via OH mailbox)
- [ ] Key epoch mismatch is detected and resolved (member requests current key from admin)

## Open Questions

1. Maximum group size? Fan-out scales linearly — at what point is it impractical?
2. Should there be multiple admins, or only one group creator?
3. How to handle conflicting group state if two admins change membership simultaneously?
4. Should key rotation use a proper group key agreement protocol (e.g. Sender Keys like Signal), or is per-member encryption sufficient?
5. How to handle the "join via link" flow — does the link go through a server, or is it purely peer-to-peer?
6. Should old messages be re-encrypted with new keys, or kept with old keys and a key archive?
