# MS07: Push Notifications

## Status: Missing

ARC42 (`02_architecture_constraints.adoc`, `03_system_scope_and_context.adoc`) identifies push notifications as essential for mobile. No implementation exists.

## Goal

Wake up the mobile client when a new message arrives at its OH, even if the app is in the background or killed. Use FCM (Android) and APNs (iOS) for content-free "wake-up" notifications that trigger a fetch. No message content leaks to Google/Apple.

## Prerequisites

- MS03 (Authenticated Encryption) — push tokens must be registered securely
- MS01 (First Real Message) — OH fetch must work to retrieve the actual message

## Current State

| What | Where | Status |
|------|-------|--------|
| Push mention in ARC42 | `02_architecture_constraints.adoc` | Documented — "mobile background restrictions require push" |
| Push provider in context | `03_system_scope_and_context.adoc` | Actor diagram includes FCM/APNs |
| Privacy concern | `11_risks_and_technical_debt.adoc` | Noted — "FCM/APNs metadata leak" |
| Flutter push packages | — | Not added to `pubspec.yaml` |
| Backend push sending | — | Missing |

## Spec

### 1. Push Token Registration

**Mobile client → OH node:**

When the mobile client registers an OH (MS01), it optionally includes a push token:

```
RegisterOhRequest (extended):
  ...existing fields...
  push_token: bytes           // FCM or APNs token (encrypted)
  push_provider: uint8        // 0=none, 1=FCM, 2=APNs
```

The push token is encrypted with the OH node's encryption public key (X25519 + AES-256-GCM) so that only the OH node can read it. This prevents other relays from learning the token.

### 2. Push Trigger (OH Node)

When `OutboundMailboxStore.addMessage()` succeeds and the OH has a registered push token:

1. Send a **content-free** push notification:
   - FCM: `data` message with `{"wake": "1"}` (no `notification` block → no user-visible alert from FCM itself).
   - APNs: `content-available: 1` with empty `alert` (silent push / background fetch).
2. Rate limit: At most 1 push per OH per 30 seconds (avoid spamming).
3. The push payload contains **no message content, no sender info, no channel info** — just a wake-up signal.

### 3. Backend Push Sender

**New component: `PushSender.java`**

```java
interface PushSender {
    void sendWakeUp(PushTarget target);
}

class FcmPushSender implements PushSender { /* Firebase Admin SDK */ }
class ApnsPushSender implements PushSender { /* APNs HTTP/2 client */ }

record PushTarget(byte[] token, PushProvider provider) {}
```

- The OH node holds the Firebase service account key / APNs auth key.
- Push sending is async (fire-and-forget) — failure is acceptable (the client will poll anyway).
- Token refresh: If FCM/APNs returns "invalid token", clear it from the OH record.

### 4. Mobile Background Fetch

**Flutter side:**

1. Use `firebase_messaging` (Android) and `flutter_apns` or `firebase_messaging` (iOS) packages.
2. On receiving a silent push:
   - Start a background Dart isolate (or use the existing `RedPandaIsolateClient`).
   - Connect to the OH node.
   - Call `fetchMessages()` for all registered OHs.
   - Decrypt and insert messages into Drift DB.
   - Show a local notification with a summary (e.g. "3 new messages").
3. On iOS: Use `BGTaskScheduler` for background fetch if silent push isn't delivered (iOS throttles silent pushes).
4. On Android: Use `WorkManager` as a fallback for periodic polling.

### 5. Token Privacy

**Threat:** Google/Apple learn "this device token received a push at time T" — metadata leak.

**Mitigations:**
- Push payload is content-free (no correlation to message content).
- Rate limiting (1 per 30s) reduces timing precision.
- Future: Consider using a push relay (a separate, untrusted server that fans out pushes) to decouple the OH node from FCM/APNs. Deferred to a later milestone.

### 6. Token Rotation

- Push tokens can change (FCM `onTokenRefresh`).
- On token change, the mobile client re-registers the OH with the new token.
- The OH node replaces the old token.

## Protobuf Changes

```protobuf
// Extend RegisterOhRequest in outbound.proto:
message RegisterOhRequest {
  bytes oh_id = 1;
  bytes oh_auth_public_key = 2;
  int64 requested_expires_at = 3;
  int64 timestamp_ms = 4;
  bytes nonce = 5;
  bytes signature = 6;
  bytes encrypted_push_token = 7;  // NEW: encrypted with OH node's enc key
  uint32 push_provider = 8;        // NEW: 0=none, 1=FCM, 2=APNs
}
```

## Backend Changes

| File | Action |
|------|--------|
| **New**: `PushSender.java` | Interface + FCM/APNs implementations |
| **New**: `PushRateLimiter.java` | Per-OH rate limiting (1 push / 30s) |
| `OutboundHandleStore.java` | Store `encrypted_push_token` + `push_provider` in `HandleRecord` |
| `OutboundService.java` | After `addMessage()`, trigger `PushSender` if token present |
| `OutboundService.java` | Decrypt push token using node's X25519 key on registration |
| **Config**: `push_config.json` | Firebase service account key, APNs auth key paths |

## Mobile Changes

| File | Action |
|------|--------|
| `pubspec.yaml` | Add `firebase_messaging`, `flutter_local_notifications` |
| **New**: `push_service.dart` | Token retrieval, registration with OH, background message handler |
| `redpanda_light_client.dart` | Include push token in `registerOutboundHandle()` |
| **New**: `background_fetch.dart` | iOS BGTaskScheduler / Android WorkManager fallback |
| `main.dart` | Initialize push service, register background handlers |
| `providers.dart` | Add `pushServiceProvider` |

## Acceptance Criteria

- [ ] App in background receives a push wake-up within 5 seconds of a message arriving at the OH
- [ ] The push payload contains no message content, sender info, or channel info
- [ ] After wake-up, the app fetches and decrypts the message in the background
- [ ] A local notification is displayed with a message count (not content)
- [ ] Push token rotation is handled: new token → re-register OH
- [ ] Invalid tokens are cleared by the OH node (FCM/APNs error feedback)
- [ ] Rate limiting: No more than 1 push per OH per 30 seconds
- [ ] iOS and Android both work (FCM for both, with APNs as alternative for iOS)
- [ ] App not installed / notifications disabled: No crash, messages still available on next foreground open via polling

## Open Questions

1. Should the OH node have direct access to FCM/APNs, or should pushes go through a separate relay service?
2. Firebase requires a Google account / project — is that acceptable for a privacy-focused app?
3. Should we support alternative push providers (e.g. UnifiedPush for de-Googled Android)?
4. How to handle iOS's aggressive throttling of silent pushes (max ~2-3 per hour)?
5. Should the local notification show the sender name or just "New message"? Showing the sender requires decryption in the background isolate.
