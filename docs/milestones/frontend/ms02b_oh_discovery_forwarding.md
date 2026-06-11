# Frontend MS02b: OH Discovery & Forwarding (Client-Anteil)

## Status: Missing (kleiner Anteil)

> **Master-Spec**: [Master-Spec im docs-Repo](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms02b_oh_discovery_forwarding.md) — MS02b ist fast vollständig Backend-Arbeit.
> **Backend-Alignment**: [Backend MS02b](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms02b_oh_discovery_forwarding.md).

## Scope (Frontend)

1. **QR-Endpoint bleibt der kurzfristige Auflösungspfad**: Der Channel-QR (v2) trägt bereits
   den `server_endpoint` des Peer-OH (seit Frontend-MS01) — keine Änderung nötig.
2. **Neue Fehler-Codes behandeln**: `RATE_LIMIT`, `QUOTA_EXCEEDED`, `BAD_REQUEST` aus
   Deposit/Register im Send-/Retry-Pfad auswerten (Backoff statt sofortigem Retry) und in der
   UI sichtbar machen (analog zur bestehenden Overflow-Warnung).
3. **DHT-basierte OH-Auflösung nutzen**, sobald das Backend sie anbietet: OH-Node eines Peers
   auflösen, wenn keine Direktverbindung zum QR-Endpoint möglich ist (Endpoint umgezogen/offline).

## Betroffene Dateien (erwartet)

| Datei | Änderung |
|-------|----------|
| `send_retry_queue.dart` | Backoff-Verhalten je Status-Code differenzieren |
| `redpanda_light_client.dart` | Status-Codes aus Deposit/Register durchreichen; OH-Lookup-API |
| UI (Chat/Snackbar) | Quota-/Rate-Limit-Feedback |

Akzeptanzkriterien und Open Questions: siehe Master-Spec.
