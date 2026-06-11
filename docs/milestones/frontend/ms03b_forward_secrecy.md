# Frontend MS03b: Forward Secrecy (Ratchet)

## Status: Missing

> **Master-Spec**: [../ms03b_forward_secrecy.md](../ms03b_forward_secrecy.md) — der
> Hauptanteil von MS03b liegt im Client.
> **Backend-Alignment**: [Backend MS03b](../backend/ms03b_forward_secrecy.md) (minimal).

## Warum

`K_enc` ist heute ein statischer, lebenslanger Channel-Key: Ein einziger Key-Leak
(Gerätebeschlagnahme, Backup, QR-Foto) kompromittiert die gesamte Vergangenheit und Zukunft
des Channels — jeder Full Node, der Payloads gespeichert hat, kann sie nachträglich
entschlüsseln.

## Scope (Frontend)

**Stage 1 — symmetrischer Ratchet (HKDF-Kette):**

1. Per-Message-Keys: `K_i = HKDF(K_{i-1}, info="redpanda-ratchet-v1")`, alte Keys sofort
   löschen; expliziter Message-Counter im authentifizierten Header (Format-v2-Erweiterung).
2. Skipped-Message-Keys für Out-of-Order-Zustellung (Store-and-Forward!) in Drift
   persistieren, mit Obergrenze und Expiry.
3. Migration: bestehende Channels von statischem `K_enc` auf Kette umstellen
   (Versions-Byte im Payload unterscheidet).

**Stage 2 — DH-Ratchet (X25519, nach Backend/Frontend MS03):**

4. Double-Ratchet-artiger Schlüsselwechsel pro Round-Trip; Spec siehe Master.

## Betroffene Dateien (erwartet)

| Datei | Änderung |
|-------|----------|
| `message_crypto_v2.dart` | Ratchet-Ableitung, Counter im MAC-geschützten Header |
| `channel.dart` | Ratchet-State statt statischem `K_enc` |
| `database.dart` | Tabellen für Ratchet-State + Skipped-Keys |
| `message_sync_service.dart` | Out-of-Order-Handling beim Entschlüsseln |

Akzeptanzkriterien und Open Questions (Multi-Device, Gruppen, Key-Backup): siehe Master-Spec.
