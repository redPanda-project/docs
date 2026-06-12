# Frontend MS03b: Forward Secrecy (Ratchet)

## Status: Done (2026-06-12 — mobile [#26](https://github.com/redPanda-project/redpanda-mobile/pull/26))

> **Master-Spec**: [Master-Spec im docs-Repo](https://github.com/redPanda-project/docs/blob/main/docs/milestones/ms03b_forward_secrecy.md) — der
> Hauptanteil von MS03b liegt im Client.
> **Backend-Alignment**: [Backend MS03b](https://github.com/redPanda-project/docs/blob/main/docs/milestones/backend/ms03b_forward_secrecy.md) (minimal).

## Warum

`K_enc` ist heute ein statischer, lebenslanger Channel-Key: Ein einziger Key-Leak
(Gerätebeschlagnahme, Backup, QR-Foto) kompromittiert die gesamte Vergangenheit und Zukunft
des Channels — jeder Full Node, der Payloads gespeichert hat, kann sie nachträglich
entschlüsseln.

## Scope (Frontend)

**Stage 1 — symmetrischer Ratchet (HKDF-Kette):**

1. Per-Message-Keys: `MK_n = HKDF(CK_n, "redpanda-fs-msg")`, `CK_{n+1} = HKDF(CK_n,
   "redpanda-fs-step")`, alte Keys sofort verworfen; expliziter Message-Counter im
   AAD-authentifizierten v4-Envelope-Header (Master-Spec Decision 2).
2. Skipped-Message-Keys für Out-of-Order-Zustellung (Store-and-Forward!) persistiert,
   mit Obergrenze und Expiry (512 pro Advance / 1024 pro Channel / 30 Tage, Decision 4).
3. Migration: Versions-Byte im Payload unterscheidet — Senden immer v4 (`0x04`),
   Lesen dispatcht v3/v4; Drift-Migration v10 ist **nicht destruktiv**.

**Stage 2 — DH-Ratchet (X25519):**

4. Double-Ratchet-Schlüsselwechsel pro Round-Trip — kombiniert mit Stage 1 in einem
   Format umgesetzt (Decision 1); Bootstrap deterministisch aus `K_enc` (Decision 3).

## Umgesetzte Dateien (mobile #26)

| Datei | Änderung |
|-------|----------|
| **Neu** `crypto/ratchet.dart` | `RatchetSession` (Stage 1+2), Skipped-Key-Store, JSON-Persistenz, Commit-on-Success |
| **Neu** `crypto/message_crypto_v4.dart` | v4-Envelope, Klartext-Header als GCM-AAD, 69 Bytes Festoverhead |
| `redpanda_light_client.dart` | v4-Encrypt beim Senden, v3/v4-Dispatch beim Fetch, `ratchetStateUpdates`-Stream, Rolle/State via `addChannelKeys` |
| `isolate_client.dart` / `isolate_protocol.dart` | Rolle + restaurierter State rein, fortgeschrittener State raus |
| `database.dart` | Drift v10: `Channels.ratchetState` (nullable, on-device only) |
| `message_sync_service.dart` | Persistiert/restauriert Ratchet-State (Ersteller-Rolle = Gerät mit `authPrivateKey`) |
| `chat_screen.dart` | Übergibt Rolle + persistierten State |

Tests: Lockstep beide Richtungen, Out-of-Order (innerhalb + über Kettengrenzen),
Replay-/maxSkip-/Tamper-Negativfälle, Persistenz-Roundtrips, Forward-Secrecy- und
Post-Compromise-Nachweise; E2E-Suiten laufen über v4 gegen das Referenz-JAR.

Akzeptanzkriterien und Decisions: siehe Master-Spec.
