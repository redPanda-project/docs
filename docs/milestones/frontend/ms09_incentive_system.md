# Frontend MS09: Reputation Client

## Status: Missing

> **Backend-Abhängigkeit**: Blocked bis [Backend MS09](../backend/ms09_incentive_system.md) Done.
> Benötigt: `CMD_REPUTATION_QUERY`, DHT-basierte Report-Speicherung, graduated PoW-Verifikation.

## Goal

Reputation-Scores abfragen und in der Hop-Selektion nutzen. Eigene Beobachtungen als Reports publizieren. PoW-Difficulty bei NodeId-Generation konfigurierbar machen.

## Prerequisites

- Frontend MS06 Done — Node-Scorer (lokale Scores aus R-ACK-Daten)
- Backend MS09 Done — ReputationService, DHT-Reports, PoW-Tiers

## Current State

| Component | File | Status |
|-----------|------|--------|
| Node Scorer (lokal) | `garlic/node_scorer.dart` (aus MS06) | Done — nur eigene Beobachtungen |
| Hop Selector | `garlic/hop_selector.dart` (aus MS04/MS06) | Done — score-basiert, nur lokal |
| NodeId PoW | — | 8 Bit (Tier 0), nicht konfigurierbar |
| Reputation Query | — | Missing |
| Report Publishing | — | Missing |

## Spec

### 1. Reputation Query

**DHT-Abfrage für Reputation-Scores:**

```dart
class ReputationClient {
  final RedPandaLightClient client;

  Future<NodeReputation> queryReputation(KademliaId nodeId) async {
    final request = NodeReputationQuery()
      ..nodeId = nodeId.bytes;

    await client.sendCommand(CMD_REPUTATION_QUERY, request.writeToBuffer());
    final response = await client.readResponse(CMD_REPUTATION_QUERY);

    return NodeReputation(
      nodeId: nodeId,
      compositeScore: response.compositeScore,
      reportCount: response.reportCount,
      powDifficulty: response.powDifficulty,
    );
  }
}
```

### 2. Report Publishing

Eigene Beobachtungen (aus Node-Scorer, MS06) als Reports in die DHT publizieren:

```dart
class ReputationReporter {
  Future<void> publishReport({
    required KademliaId subjectId,
    required ObservationType type,
    Uint8List? data,
  }) async {
    final report = ReputationReport()
      ..reporterId = myKademliaId.bytes
      ..subjectId = subjectId.bytes
      ..observationType = type.value
      ..timestamp = DateTime.now().millisecondsSinceEpoch
      ..data = data ?? Uint8List(0);

    // Signieren
    report.signature = CryptoUtils.sign(mySigningKey, report.signingBytes);

    // In DHT speichern (via KademliaStore)
    final kadKey = sha256('reputation${hex(subjectId)}${todayDateString}').sublist(0, 20);
    await client.kadStore(kadKey, report.writeToBuffer(), myKeypair);
  }
}
```

**Automatisches Publishing (Hintergrund-Job):**

```dart
Timer.periodic(Duration(hours: 1), (_) async {
  final scores = await nodeScorer.getAllScores();
  for (final score in scores) {
    if (score.totalInteractions > 5) { // Mindestens 5 Beobachtungen
      final type = score.successRate > 0.7
        ? ObservationType.relaySuccess
        : ObservationType.relayFailure;
      await reputationReporter.publishReport(
        subjectId: score.nodeId,
        type: type,
      );
    }
  }
});
```

### 3. Enhanced Hop Selector

**`hop_selector.dart` — Reputation-Integration:**

```dart
List<PeerInfo> selectHops({...}) {
  final candidates = ...;

  // Lokalen Score + globale Reputation kombinieren
  for (final candidate in candidates) {
    final localScore = nodeScorer.getCachedScore(candidate.nodeId) ?? 0.5;
    final globalRep = reputationCache.get(candidate.nodeId);

    candidate.combinedScore = globalRep != null
      ? 0.6 * localScore + 0.4 * globalRep.compositeScore
      : localScore;
  }

  // Nodes mit Score < 0.3 ausschließen
  candidates.removeWhere((c) => c.combinedScore < 0.3);

  // Gewichtete Zufallsauswahl (höhere Scores = höhere Wahrscheinlichkeit)
  return weightedRandomSelection(candidates, count: 3);
}
```

### 4. Reputation Cache

Globale Reputation-Scores cachen (DHT-Queries sind teuer):

```dart
class ReputationCache {
  final Map<String, CachedReputation> _cache = {};
  static const cacheDuration = Duration(hours: 6);

  Future<NodeReputation?> get(KademliaId nodeId) async {
    final cached = _cache[nodeId.hex];
    if (cached != null && !cached.isExpired) return cached.reputation;

    // DHT Query
    try {
      final rep = await reputationClient.queryReputation(nodeId);
      _cache[nodeId.hex] = CachedReputation(rep, DateTime.now());
      await db.cacheNodeReputation(nodeId, rep); // Persist
      return rep;
    } catch (e) {
      // Fallback auf persistierten Cache
      return await db.getCachedReputation(nodeId);
    }
  }
}
```

### 5. PoW-Difficulty bei NodeId-Generation

**Konfigurierbare PoW-Difficulty für Mobile:**

```dart
class NodeIdGenerator {
  /// Generiert eine NodeId mit der gewünschten PoW-Difficulty.
  /// Tier 0 (8 bits) = instant
  /// Tier 1 (16 bits) = Sekunden
  /// Tier 2 (20 bits) = Minuten (mit Progress-Callback)
  static Future<NodeId> generate({
    int targetBits = 8,
    void Function(int attempts)? onProgress,
  }) async {
    int attempts = 0;
    while (true) {
      final keypair = CryptoUtils.generateSigningKeypair();
      final hash = sha256(sha256(keypair.public));
      final leadingZeros = countLeadingZeroBits(hash);

      attempts++;
      if (attempts % 1000 == 0) onProgress?.call(attempts);

      if (leadingZeros >= targetBits) {
        return NodeId(keypair: keypair, powDifficulty: leadingZeros);
      }
    }
  }
}
```

**UI: Erste App-Nutzung — PoW-Auswahl:**

| Option | Dauer | Vorteil |
|--------|-------|---------|
| Schnellstart (Tier 0) | Sofort | Kein Warten |
| Standard (Tier 1) | ~10 Sekunden | Etwas mehr Vertrauen |
| Hohe Reputation (Tier 2) | ~5 Minuten | Deutlich mehr Vertrauen |

Progress-Bar während der PoW-Generation.

### 6. PoW-Verifikation

Beim Empfang von `PeerInfoProto` den PoW-Tier des Peers verifizieren:

```dart
int verifyPoW(Uint8List verifyKey) {
  final hash = sha256(sha256(verifyKey));
  return countLeadingZeroBits(hash);
}
```

Speichern in `Peers` Table zur Anzeige und für Scoring.

### 7. Database Migration

**Schema v12:**
- `Peers` Table: `pow_difficulty INTEGER DEFAULT 0`
- Neue Table `reputation_cache`: `node_id TEXT PK`, `composite_score REAL`, `pow_difficulty INT`, `report_count INT`, `last_updated DATETIME`
- Neue Table `published_reports`: `id INTEGER PK`, `subject_id TEXT`, `type INT`, `timestamp INT` (um Duplikate zu vermeiden)

## Mobile Changes

| File | Action |
|------|--------|
| **New**: `reputation/reputation_client.dart` | DHT-basierte Reputation-Abfrage |
| **New**: `reputation/reputation_reporter.dart` | Eigene Reports publizieren |
| **New**: `reputation/reputation_cache.dart` | 6h Cache für globale Scores |
| **New**: `crypto/node_id_generator.dart` | Konfigurierbare PoW-Difficulty |
| `garlic/hop_selector.dart` | Lokale + globale Scores kombinieren, Minimum-Score-Filter |
| `garlic/node_scorer.dart` | Reports periodisch publizieren |
| `database.dart` | Migration v12: `reputation_cache`, `published_reports`, `Peers.pow_difficulty` |
| `providers.dart` | `reputationClientProvider`, `reputationCacheProvider` |
| **New** (optional): `screens/debug/reputation_screen.dart` | Debug-Ansicht: bekannte Node-Reputationen |

## Acceptance Criteria

- [ ] Reputation eines Nodes via `CMD_REPUTATION_QUERY` abfragbar
- [ ] Hop-Selektion kombiniert lokale Scores (60%) + globale Reputation (40%)
- [ ] Nodes mit `combinedScore < 0.3` werden als Hops ausgeschlossen
- [ ] Eigene Beobachtungen werden als signierte Reports in die DHT publiziert
- [ ] Self-Reports werden beim Publishing verhindert
- [ ] PoW-Difficulty bei NodeId-Generation wählbar (Tier 0–2)
- [ ] PoW-Tier wird in der Peer-Liste verifiziert und gespeichert
- [ ] Reputation-Cache persistiert (6h TTL, Drift-backed)
- [ ] Reputation-Abfragen blockieren nicht den UI-Thread (async + cached)

## Open Questions

1. Soll die PoW-Auswahl beim Onboarding oder in den Settings sein?
2. Wie viel Gewicht globale vs. lokale Reputation? 40/60 oder anpassbar?
3. Soll der User Reputationen sehen können (Debug/Power-User), oder ist das nur intern?
4. Wie mit Reputation-Data umgehen, wenn die DHT-Query fehlschlägt? Nur lokale Scores?
5. Tier 2 PoW (Minuten) auf älteren Phones — akzeptabel?
