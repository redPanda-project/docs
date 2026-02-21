# Frontend MS08: Group Chat

## Status: Missing

> **Backend-Abhängigkeit**: Keine — [Backend MS08](../backend/ms08_group_chat.md) hat keine Backend-Änderungen.
> Group Chat ist reine Frontend-Logik. Blocked bis Frontend MS05 Done (RGBs für Reply-Paths in der Gruppe).

## Goal

Gruppen-Konversationen mit 3+ Teilnehmern. Fan-Out: Nachrichten an jedes Mitglieds-OH senden. Key Rotation bei Mitglieder-Änderungen. Group Invite via QR-Code.

## Prerequisites

- Frontend MS05 Done — RGB für Reply-Paths
- Frontend MS04 Done — Garlic-Routing für Fan-Out
- Frontend MS02 Done — Zuverlässige Zustellung an jedes OH

## Current State

| Component | File | Status |
|-----------|------|--------|
| Channel model | `channel.dart` | 1-to-1 only |
| Channels table | `database.dart` | Kein Group-Support |
| Chat screen | `chat_screen.dart` | Zeigt einen Peer |

## Spec

### 1. GroupChannel Model

**Neue Datei `domain/group_channel.dart`:**

```dart
class GroupChannel extends Equatable {
  final String groupId;              // 32-byte hex
  final String label;
  final List<int> encryptionKey;     // 32 bytes, rotiert bei Membership-Change
  final Ed25519Keypair authKeypair;  // Für Signing von Group-Metadata
  final int keyEpoch;                // Inkrementiert bei Key Rotation
  final List<GroupMember> members;

  String get id => sha256([...encryptionKey, ...authKeypair.public]).hex;
}

class GroupMember extends Equatable {
  final String memberId;             // 32-byte hex
  final String displayName;
  final OHDescriptor ohDescriptor;
  final List<int> encryptionPublicKey; // 32 bytes X25519
  final GroupRole role;               // ADMIN | MEMBER
}

enum GroupRole { admin, member }
```

### 2. Group Creation

```dart
Future<GroupChannel> createGroup(String name, List<Contact> initialMembers) async {
  final groupId = SecureRandom(32).hex;
  final encryptionKey = SecureRandom(32).bytes;
  final authKeypair = CryptoUtils.generateSigningKeypair();

  final me = GroupMember(
    memberId: SecureRandom(32).hex,
    displayName: myDisplayName,
    ohDescriptor: myOHDescriptor,
    encryptionPublicKey: myEncryptionPublicKey,
    role: GroupRole.admin,
  );

  final group = GroupChannel(
    groupId: groupId,
    label: name,
    encryptionKey: encryptionKey,
    authKeypair: authKeypair,
    keyEpoch: 0,
    members: [me],
  );

  await db.insertGroupChannel(group);

  // Invites an initiale Mitglieder senden
  for (final contact in initialMembers) {
    await sendGroupInvite(group, contact);
  }

  return group;
}
```

### 3. Group Invite

```dart
Future<void> sendGroupInvite(GroupChannel group, Contact contact) async {
  final invite = GroupInvite(
    groupId: group.groupId,
    groupName: group.label,
    encryptionKey: group.encryptionKey,
    keyEpoch: group.keyEpoch,
    members: group.members,
  );

  // Per-Member verschlüsselt (nur der Eingeladene kann lesen)
  final encrypted = CryptoUtils.aesGcmEncrypt(
    CryptoUtils.x25519(myEncryptionKey, contact.encryptionPublicKey),
    nonce, invite.serialize(), Uint8List(0),
  );

  // Als reguläre Nachricht über Contact's OH senden
  await sendToOH(contact.ohDescriptor, encrypted);
}
```

**QR-Code Invite (Alternative):**
```json
{
  "type": "group_invite",
  "group_id": "hex...",
  "group_name": "Name",
  "k_enc": "hex...",
  "key_epoch": 0,
  "members": [...],
  "inviter_oh": { "ep": "...", "id": "...", "pk": "..." }
}
```

### 4. Fan-Out Sending

```dart
Future<void> sendGroupMessage(GroupChannel group, String content) async {
  // 1. Mit Gruppen-K_enc verschlüsseln
  final channelMsg = ChannelMessage()
    ..messageId = generateMessageId()
    ..content = utf8.encode(content)
    ..timestamp = DateTime.now().millisecondsSinceEpoch;
  final encrypted = encryptChannelMessage(group, channelMsg.writeToBuffer());

  // 2. An jedes Mitglied senden (außer mir selbst)
  for (final member in group.members) {
    if (member.memberId == myMemberId) continue;

    // Garlic-Paket an Member's OH
    final hops = hopSelector.selectHops(
      destination: member.ohDescriptor.nodeKademliaId,
      myNodeId: myKademliaId,
    );
    final packet = garlicBuilder.build(
      hops: hops,
      destination: member.ohDescriptor.nodeKademliaId,
      ohId: member.ohDescriptor.handleId,
      payload: encrypted,
    );
    await sendToNode(hops[0], packet);
  }

  // 3. Lokal speichern
  await db.insertMessage(group.id, myMemberId, content, status: MessageStatus.sent);
}
```

### 5. Key Rotation

```dart
Future<void> rotateGroupKey(GroupChannel group) async {
  final newKey = SecureRandom(32).bytes;
  final newEpoch = group.keyEpoch + 1;

  // KeyRotation Control Message an jedes Mitglied
  for (final member in group.members) {
    if (member.memberId == myMemberId) continue;

    final rotation = KeyRotation(
      newEncryptionKey: newKey,
      newKeyEpoch: newEpoch,
      members: group.members,
    );

    // Per-Member verschlüsselt (mit dem Member's X25519 Key)
    final encrypted = CryptoUtils.aesGcmEncrypt(
      CryptoUtils.x25519(myEncryptionKey, member.encryptionPublicKey),
      nonce, rotation.serialize(), Uint8List(0),
    );

    await sendToOH(member.ohDescriptor, encrypted);
  }

  // Lokale Gruppe updaten
  await db.updateGroupChannel(group.groupId,
    encryptionKey: newKey,
    keyEpoch: newEpoch,
  );
}
```

**Auslöser für Key Rotation:**
- Member hinzugefügt → rotieren (neues Mitglied bekommt neuen Key via Invite)
- Member entfernt → rotieren (entferntes Mitglied bekommt neuen Key NICHT)
- Manuell (Admin-Aktion)
- Periodisch (optional, z.B. alle 7 Tage)

### 6. Member Add/Remove

```dart
Future<void> addMember(GroupChannel group, Contact newMember) async {
  // 1. Key Rotation
  await rotateGroupKey(group);

  // 2. Invite an neues Mitglied
  await sendGroupInvite(group, newMember);

  // 3. MemberAdded Control Message an alle bestehenden Mitglieder
  final controlMsg = GroupControl.memberAdded(GroupMember(
    memberId: SecureRandom(32).hex,
    displayName: newMember.name,
    ohDescriptor: newMember.ohDescriptor,
    encryptionPublicKey: newMember.encryptionPublicKey,
    role: GroupRole.member,
  ));
  await sendGroupControlMessage(group, controlMsg);
}

Future<void> removeMember(GroupChannel group, String memberId) async {
  // 1. Member aus lokaler Liste entfernen
  group.members.removeWhere((m) => m.memberId == memberId);

  // 2. Key Rotation (entferntes Mitglied bekommt neuen Key nicht)
  await rotateGroupKey(group);

  // 3. MemberRemoved Control Message an verbleibende Mitglieder
  final controlMsg = GroupControl.memberRemoved(memberId);
  await sendGroupControlMessage(group, controlMsg);
}
```

### 7. Control Message Handling

```dart
Future<void> handleGroupControl(GroupChannel group, GroupControl control) async {
  switch (control.type) {
    case GroupControlType.memberAdded:
      await db.addGroupMember(group.groupId, control.member);
      break;
    case GroupControlType.memberRemoved:
      await db.removeGroupMember(group.groupId, control.memberId);
      break;
    case GroupControlType.keyRotation:
      await db.updateGroupChannel(group.groupId,
        encryptionKey: control.newEncryptionKey,
        keyEpoch: control.newKeyEpoch,
      );
      break;
    case GroupControlType.infoUpdate:
      await db.updateGroupChannel(group.groupId,
        label: control.name,
      );
      break;
  }
}
```

### 8. Chat Screen für Gruppen

- Sender-Name pro Nachricht anzeigen (aus `GroupMember.displayName`).
- Member-Count im Header.
- "Info"-Button → Group Info Screen (Member-Liste, Admin-Controls).

### 9. Database Migration

**Schema v11:**
```
group_channels:
  group_id TEXT PK
  label TEXT
  encryption_key TEXT (hex)
  auth_private_key TEXT (hex)
  auth_public_key TEXT (hex)
  key_epoch INTEGER

group_members:
  id INTEGER PK AUTOINCREMENT
  group_id TEXT → group_channels.group_id
  member_id TEXT
  display_name TEXT
  oh_endpoint TEXT
  oh_handle_id TEXT (hex)
  oh_auth_public_key TEXT (hex)
  encryption_public_key TEXT (hex)
  role INTEGER (0=admin, 1=member)

key_archive:
  id INTEGER PK AUTOINCREMENT
  group_id TEXT → group_channels.group_id
  encryption_key TEXT (hex)
  key_epoch INTEGER
  valid_from DATETIME
```

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `domain/group_channel.dart` | GroupChannel + GroupMember Models |
| **New**: `group/group_service.dart` | Create, Join, Leave, Fan-Out, Key Rotation |
| **New**: `screens/group/group_invite_screen.dart` | Group-Invite erstellen/akzeptieren |
| **New**: `screens/group/group_info_screen.dart` | Member-Liste, Admin-Controls |
| `chat_screen.dart` | Sender-Name pro Nachricht, Member-Count, Group vs 1:1 Unterscheidung |
| `database.dart` | Migration v11: `group_channels`, `group_members`, `key_archive` Tables |
| `client/redpanda_light_client.dart` | `sendGroupMessage()` mit Fan-Out |
| `providers.dart` | `groupServiceProvider`, `groupMembersProvider(groupId)` |

## Acceptance Criteria

- [ ] Gruppe mit 3+ Mitgliedern erstellen → alle Mitglieder empfangen Invite
- [ ] Nachricht an Gruppe → alle Mitglieder empfangen die Nachricht
- [ ] Sender-Name wird pro Nachricht im Chat angezeigt
- [ ] Member hinzufügen → Key Rotation → neues Mitglied bekommt neuen Key
- [ ] Member entfernen → Key Rotation → entferntes Mitglied kann neue Nachrichten nicht lesen
- [ ] Alte Nachrichten bleiben mit altem Key lesbar (Key Archive)
- [ ] Group Invite via QR-Code funktioniert
- [ ] Offline-Mitglieder empfangen Nachrichten beim nächsten Online-Gehen
- [ ] Key Epoch Mismatch wird erkannt → Member fordert aktuellen Key vom Admin an

## Open Questions

1. Maximale Gruppengröße? Fan-Out skaliert linear — ab wann unpraktisch?
2. Mehrere Admins oder nur ein Creator?
3. Sender Keys (wie Signal) statt per-Member Encryption — lohnt sich die Komplexität?
4. Wie mit Key-Konflikten umgehen, wenn zwei Admins gleichzeitig rotieren?
5. Soll der Group-Name End-to-End verschlüsselt sein (Server sieht ihn nie)?
