# Frontend MS07: Push Registration & Background Fetch

## Status: Missing

> **Backend-Abhängigkeit**: Blocked bis [Backend MS07](../backend/ms07_push_notifications.md) Done.
> Benötigt: Push-Token-Verarbeitung bei OH-Registration, FCM/APNs Wake-Up Sending.

## Goal

Push-Token bei OH-Registration mitschicken. Auf Wake-Up Push reagieren: Background Fetch auslösen, Nachrichten holen, lokale Notification anzeigen. iOS und Android.

## Prerequisites

- Frontend MS01 Done — OH-Registration + fetchMessages()
- Backend MS07 Done — Push-Token-Verarbeitung + FCM/APNs Sender

## Current State

| Component | File | Status |
|-----------|------|--------|
| OH Registration | `redpanda_light_client.dart` | Done (aus MS01) — kein Push-Token |
| Background Fetch | — | Missing |
| Push Packages | `pubspec.yaml` | Nicht vorhanden |
| Local Notifications | — | Missing |

## Spec

### 1. Push-Token Retrieval

**Neue Datei `push/push_service.dart`:**

```dart
class PushService {
  final FirebaseMessaging _messaging = FirebaseMessaging.instance;

  Future<String?> getToken() async {
    // iOS: Request permission first
    final settings = await _messaging.requestPermission(
      alert: true,
      badge: true,
      sound: true,
    );
    if (settings.authorizationStatus == AuthorizationStatus.denied) return null;

    return await _messaging.getToken();
  }

  Stream<String> get onTokenRefresh => _messaging.onTokenRefresh;
}
```

### 2. Push-Token bei OH-Registration

**`RedPandaLightClient.registerOutboundHandle()` — Erweiterung:**

```dart
Future<OHRegistration> registerOutboundHandle() async {
  final pushToken = await pushService.getToken();

  final request = RegisterOhRequest()
    ..ohId = ohId
    ..ohAuthPublicKey = keypair.publicKeyBytes
    ..requestedExpiresAt = ...
    ..timestampMs = ...
    ..nonce = ...
    ..signature = ...;

  // Push-Token verschlüsselt mitschicken
  if (pushToken != null) {
    final ohNodeEncPubKey = await getOHNodeEncryptionKey();
    request.encryptedPushToken = CryptoUtils.aesGcmEncrypt(
      CryptoUtils.x25519(ephemeralKey, ohNodeEncPubKey),
      nonce, utf8.encode(pushToken), Uint8List(0),
    );
    request.pushProvider = Platform.isIOS ? 2 : 1; // 1=FCM, 2=APNs
  }

  await sendCommand(CMD_REGISTER_OH, request.writeToBuffer());
  // ...
}
```

### 3. Background Message Handler

**`main.dart`:**

```dart
// Top-level function (muss außerhalb von Klassen sein für Isolate)
@pragma('vm:entry-point')
Future<void> _firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  // App ist im Background oder killed
  // 1. Drift DB öffnen
  final db = AppDatabase();

  // 2. OH-Registrierungen laden
  final ohs = await db.getOutboundHandles();

  // 3. Für jedes OH: fetchMessages()
  final client = RedPandaLightClient(seeds: defaultSeeds);
  await client.connect();

  for (final oh in ohs) {
    try {
      final messages = await client.fetchMessages(oh);
      for (final msg in messages) {
        await db.insertMessageIfNew(msg);
      }
    } catch (e) {
      // Retry beim nächsten Push oder Foreground-Open
    }
  }

  await client.disconnect();

  // 4. Lokale Notification anzeigen
  if (totalNewMessages > 0) {
    await showLocalNotification(
      title: 'Redpanda',
      body: '$totalNewMessages neue Nachricht${totalNewMessages > 1 ? "en" : ""}',
    );
  }
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp();
  FirebaseMessaging.onBackgroundMessage(_firebaseMessagingBackgroundHandler);
  // ...
}
```

### 4. Foreground Message Handler

```dart
// In App-State (z.B. via Provider):
FirebaseMessaging.onMessage.listen((RemoteMessage message) {
  // App ist im Foreground — sofort fetchen
  ref.read(redPandaClientProvider).fetchAllOHs();
});
```

### 5. Token Refresh

```dart
// In PushService:
void startTokenRefreshListener() {
  _messaging.onTokenRefresh.listen((newToken) async {
    // OH neu registrieren mit neuem Token
    for (final oh in registeredOHs) {
      await registerOutboundHandle(existingOhId: oh.ohId, pushToken: newToken);
    }
  });
}
```

### 6. Local Notifications

**Package: `flutter_local_notifications`**

```dart
class LocalNotificationService {
  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();

  Future<void> init() async {
    const androidSettings = AndroidInitializationSettings('@mipmap/ic_launcher');
    const iosSettings = DarwinInitializationSettings();
    await _plugin.initialize(InitializationSettings(
      android: androidSettings, iOS: iosSettings,
    ));
  }

  Future<void> showNewMessageNotification(int count) async {
    await _plugin.show(
      0, 'Redpanda',
      '$count neue Nachricht${count > 1 ? "en" : ""}',
      NotificationDetails(
        android: AndroidNotificationDetails('messages', 'Messages'),
        iOS: DarwinNotificationDetails(),
      ),
    );
  }
}
```

### 7. iOS Background Fetch Fallback

iOS throttles silent pushes (max ~2-3/Stunde). Fallback:

```dart
// In main.dart oder AppDelegate:
void registerBackgroundTask() {
  Workmanager().registerPeriodicTask(
    'redpanda_fetch',
    'fetchMessages',
    frequency: Duration(minutes: 15),
    constraints: Constraints(networkType: NetworkType.connected),
  );
}
```

### 8. Platform-spezifische Config

**Android:**
- `android/app/google-services.json` — Firebase Config
- `AndroidManifest.xml` — Push Permission

**iOS:**
- `ios/Runner/GoogleService-Info.plist` — Firebase Config
- `ios/Runner/Runner.entitlements` — Push Notification Entitlement
- Background Modes: `remote-notification`, `fetch`

## Mobile Changes

| File | Action |
|------|--------|
| `pubspec.yaml` | `firebase_core`, `firebase_messaging`, `flutter_local_notifications`, `workmanager` |
| **New**: `push/push_service.dart` | Token-Retrieval, Refresh-Listener |
| **New**: `push/local_notification_service.dart` | Lokale Notifications anzeigen |
| **New**: `push/background_fetch.dart` | iOS/Android Background Fetch Fallback |
| `client/redpanda_light_client.dart` | Push-Token in `registerOutboundHandle()` |
| `main.dart` | Firebase init, Background-Handler registrieren |
| `providers.dart` | `pushServiceProvider`, `localNotificationProvider` |
| `android/app/google-services.json` | Firebase Config (NEW) |
| `ios/Runner/GoogleService-Info.plist` | Firebase Config (NEW) |

## Acceptance Criteria

- [ ] Push-Token wird bei OH-Registration verschlüsselt an den Server geschickt
- [ ] App im Background: Wake-Up Push → Background Fetch → Nachrichten in DB → lokale Notification
- [ ] App im Foreground: Wake-Up Push → sofortiger Fetch (keine lokale Notification)
- [ ] Push-Payload enthält keinen Message-Content (nur `{"wake": "1"}`)
- [ ] Token-Refresh → automatische Re-Registration bei allen OHs
- [ ] iOS: Background Fetch Fallback (WorkManager, alle 15 Min)
- [ ] Android: Wake-Up Push funktioniert auch bei App-Kill
- [ ] Lokale Notification zeigt nur Anzahl ("3 neue Nachrichten"), keinen Content
- [ ] Notifications deaktiviert → kein Crash, Nachrichten beim nächsten Foreground-Open verfügbar

## Open Questions

1. Firebase erfordert Google-Konto — akzeptabel für eine Privacy-App?
2. UnifiedPush als Alternative für de-Googled Android?
3. Soll die lokale Notification den Sender-Namen zeigen? Erfordert Decryption im Background.
4. Wie mit iOS-Throttling umgehen — ist WorkManager (15 Min) ausreichend?
