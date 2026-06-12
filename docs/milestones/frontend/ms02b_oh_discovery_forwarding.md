# Frontend MS02b: OH Discovery & Forwarding (Client-Anteil)

## Status: Done (2026-06-12) — umgesetzt in [mobile #20](https://github.com/redPanda-project/redpanda-mobile/pull/20)

> **Umsetzungsnotizen (2026-06-12):** `sendMessage()` setzt `want_response` und wertet die
> `FlaschenpostPutResponse` (Command 158) aus — Rejections werfen eine typisierte
> `DepositException` (durchs Isolate transportiert); ohne Antwort innerhalb von 10 s gilt das
> Legacy-Fire-and-forget-Verhalten (Kompatibilität mit pre-MS02b-Nodes, Dedup via `message_id`).
> Retry-Differenzierung in `send_retry_queue.dart`: `BAD_REQUEST` (> 64 KiB) → permanent
> failed, `QUOTA_EXCEEDED` → verlängerter Backoff (≥ 8 min), `NOT_FOUND` → normaler Backoff.
> `registerOutboundHandle()` wartet die `RegisterOhResponse` ab (`RATE_LIMIT` →
> `RateLimitException`, OK übernimmt die Server-Expiry). UI: Snackbar analog zur
> Overflow-Warnung. Punkt 3 (Deposit an irgendeinen verbundenen Node) war durch das
> Backend-Forwarding bereits abgedeckt; eine clientseitige DHT-Auflösung war nicht nötig.

> **Master-Spec**: [Master-Spec im docs-Repo](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms02b_oh_discovery_forwarding.md) — MS02b ist fast vollständig Backend-Arbeit.
> **Backend-Alignment**: [Backend MS02b](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms02b_oh_discovery_forwarding.md).

## Scope (Frontend)

1. **QR-Endpoint bleibt der kurzfristige Auflösungspfad**: Der Channel-QR (v2) trägt bereits
   den `server_endpoint` des Peer-OH (seit Frontend-MS01) — keine Änderung nötig.
2. **Neue Fehler-Codes behandeln**: `RATE_LIMIT`, `QUOTA_EXCEEDED`, `BAD_REQUEST` aus
   Deposit/Register im Send-/Retry-Pfad auswerten (Backoff statt sofortigem Retry) und in der
   UI sichtbar machen (analog zur bestehenden Overflow-Warnung).
3. **OH-Auflösung über das Netz — serverseitig gelöst**: Der Client deposited an irgendeinen
   verbundenen Full Node; der Node löst den OH-Host selbst über den DHT-Announce-Record auf
   und forwarded (Option A, max. 3 Hops). Eine clientseitige DHT-Auflösung war damit nicht
   nötig; der QR-Endpoint (Punkt 1) bleibt der Pfad für die Direktverbindung.

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
