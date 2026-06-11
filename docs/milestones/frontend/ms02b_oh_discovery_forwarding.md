# Frontend MS02b: OH Discovery & Forwarding (Client-Anteil)

## Status: Missing (kleiner Anteil) — Backend seit 2026-06-11 Done, kann starten

> **Master-Spec**: [Master-Spec im docs-Repo](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms02b_oh_discovery_forwarding.md) — MS02b ist fast vollständig Backend-Arbeit.
> **Backend-Alignment**: [Backend MS02b](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms02b_oh_discovery_forwarding.md).

## Scope (Frontend)

1. **QR-Endpoint bleibt der kurzfristige Auflösungspfad**: Der Channel-QR (v2) trägt bereits
   den `server_endpoint` des Peer-OH (seit Frontend-MS01) — keine Änderung nötig.
2. **Neue Fehler-Codes behandeln**: `RATE_LIMIT`, `QUOTA_EXCEEDED`, `BAD_REQUEST` aus
   Deposit/Register im Send-/Retry-Pfad auswerten (Backoff statt sofortigem Retry) und in der
   UI sichtbar machen (analog zur bestehenden Overflow-Warnung).
3. **DHT-basierte OH-Auflösung nutzen** (Backend bietet sie seit MS02b an): OH-Node eines Peers
   auflösen, wenn keine Direktverbindung zum QR-Endpoint möglich ist (Endpoint umgezogen/offline).
   Kurzfristig reicht clientseitig: Deposit an irgendeinen verbundenen Full Node — der Node
   forwarded selbst zum OH-Host (Option A, max. 3 Hops).

**Konkrete Backend-API (seit MS02b):** `FlaschenpostPut.want_response = true` (Feld 3) setzen,
dann antwortet der direkt verbundene Node mit `FlaschenpostPutResponse` (Command 158):
`OK` = deposited oder zum Forwarding angenommen, `NOT_FOUND` = nicht zustellbar (Hop-Limit),
`QUOTA_EXCEEDED` = Mailbox voll (reject-new — nichts wurde verdrängt), `BAD_REQUEST` =
Item > 64 KiB. `RegisterOhResponse` kann jetzt `RATE_LIMIT` liefern (5/min pro Verbindung).

## Betroffene Dateien (erwartet)

| Datei | Änderung |
|-------|----------|
| `send_retry_queue.dart` | Backoff-Verhalten je Status-Code differenzieren |
| `redpanda_light_client.dart` | Status-Codes aus Deposit/Register durchreichen; OH-Lookup-API |
| UI (Chat/Snackbar) | Quota-/Rate-Limit-Feedback |

Akzeptanzkriterien und Open Questions: siehe Master-Spec.
