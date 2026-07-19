# Redpanda – Begriffe & Definitionen (Draft)

> **Hinweis:** Das ist ein konsolidierter Stand aus unserem aktuellen Gespräch. Begriffe/Details können sich beim Protokolldesign noch präzisieren.

## 1) Channel

Ein **Channel** ist eine *server-agnostische* kryptografische Kommunikationsdomäne.  
Technisch ist ein Channel **zunächst ein Keyset**, das bestimmt, wer Channel-Inhalte lesen/schreiben kann.

### Channel-Keyset (aktueller Stand)
- **`K_enc` (AES)**: symmetrischer Schlüssel zur **Verschlüsselung** von Channel-Inhalten (z.B. OH-Descriptoren, Channel-Metadaten, ggf. Nachrichten).
- **`K_auth` (privater Key, aktuell als „privater ECDH-Key“ beschrieben)**: Schlüssel zur **Authentisierung/Signierung** von Channel-bezogenen Objekten.

> **Anmerkung für spätere Präzisierung:** ECDH ist klassisch Key-Agreement, nicht Signatur. Wir können das später sauber als „Signierschlüssel“ (z.B. Ed25519/ECDSA) oder als getrennte Auth-Mechanik definieren. Für diesen Draft bleibt es bei „Auth/Signing-Key“.

### Teilnehmer
Ein Channel kann **n ∈ ℕ** Teilnehmer haben.  
**Teilnehmer ist, wer das Channel-Keyset kennt.**

### Eigenschaften
- Ein Channel hat **keine direkte Bindung an einen Server**.
- Ein Channel ist der **Verteil-/Vertrauenskontext**, über den z.B. Outbound Handles geteilt werden.

---

## 2) Garlic Message

Eine **Garlic Message** ist eine mehrschichtige (onion-/garlic-artige) Nachricht, die über mehrere Hops geroutet wird.  
In unserem Modell gibt es eine **letzte Schicht**, die am Ende an einem Outbound Handle geöffnet wird.

Begriffe:
- **Hop**: ein Weiterleitungsschritt (z.B. Relay/Node) auf dem Weg zum Ziel.
- **„3 Hops“**: die Nachricht wird über drei Stationen bis zum Outbound Server zugestellt.

---

## 3) Outbound Server

Ein **Outbound Server** ist ein Server, der **Outbound Handles (OH)** hostet.

### Hauptaufgaben
- **Registrierung** neuer Outbound Handles für Clients (z.B. Alice’ Light Client).
- **Annahme** eingehender Abgaben an ein Outbound Handle.
- **Autorisierung**: prüft, ob der Sender berechtigt ist (typischerweise über Signaturprüfung gegen OH-Auth-Infos).
- **Öffnen der letzten Garlic-Schicht**: extrahiert aus der finalen Schicht
  - **`push`** (Push-Nachricht / Zustellpayload)
  - **`ack`** (Quittung / Zustellbestätigung / ACK-Block, je nach Semantik)

> Wichtig: Der Outbound Server kennt *nicht automatisch* den Channel. Er arbeitet primär auf OH-Ebene.

---

## 4) Outbound Handle (OH)

Ein **Outbound Handle (OH)** ist ein *servergebundener Zustell-Endpunkt* mit eigener Autorisierung.

Ein OH ist „die Adresse + Capability“, an die eine (geroutete) Garlic-Kette am Ende zugestellt wird, damit der Outbound Server dort die **letzte Garlic-Schicht** öffnen kann.

### OH hat ein eigenes Auth-Keyset
**Wichtig (Korrektur gegenüber früherer Formulierung):**  
Ein OH ist **nicht** direkt an das Channel-Keyset gekoppelt.  
Stattdessen hat **jedes OH ein eigenes Auth-Keyset**, das **über Channels geteilt** wird.

Beispielhafte Bestandteile:
- **`OH_auth_pub`**: Public Key (oder prüfbares Token), gegen das der Outbound Server die Autorisierung prüft.
- **`OH_auth_priv` / Secret**: Private Key/Token, den berechtigte Sender besitzen müssen, um am OH abzugeben.
- Optional: **Policy/Expiry/Rate Limits** (z.B. Ablaufzeit, max. Nachrichtengröße).

### Semantik
- **Wer an ein OH senden darf**, bestimmt allein das **OH-Auth-Keyset** (nicht „automatisch“ der Channel).
- Praktisch erhalten meist nur Channel-Teilnehmer das OH-Auth-Material, weil es **im Channel** verteilt wird.

---

## 5) OH-Descriptor (OHDescriptor)

Ein **OHDescriptor** ist ein Datenpaket, das alle Informationen enthält, die ein Teilnehmer braucht, um ein OH zu nutzen.

Beispiel-Felder (Draft):
- `server_endpoint` (Host/Port/Transport)
- `handle_id` (opaque ID am Outbound Server)
- `OH_auth_pub` (oder Verifikationsmaterial)
- `policy` / `expiry` / `limits` (optional)

**Distribution:**  
OHDescriptor wird typischerweise **über den Channel** verteilt (verschlüsselt mit `K_enc`, ggf. signiert mit `K_auth`).

---

## 6) DHT & Rendezvous

Die **DHT** dient als *Einstieg/Discovery*, um initial „irgendeinen“ erreichbaren Anknüpfungspunkt zu finden.

### Ziel
- Ein neuer Teilnehmer muss theoretisch nur **einen** weiteren erreichbaren „Anknüpfungspunkt“ finden, um in Kommunikation zu kommen.

### Begriffsvorschlag für „Anknüpfungspunkt“
Statt „Kontakt“ (Person) meinen wir hier: *Outbound Server/Endpoint + benötigte Daten*.  
Das ist in diesem Dokument durch **OHDescriptor** (oder allgemein „Rendezvous Record“) abgebildet.

### Rendezvous Record (Draft)
Ein DHT-Eintrag, der einen Einstieg ermöglicht, ohne Secrets offen zu legen.  
Praktisch: Inhalte im DHT **verschlüsselt** (z.B. mit Channel-`K_enc`), sodass nur Channel-Teilnehmer ihn lesen können.

> **Konkretisiert (2026-07-19, T43):** Der Rendezvous Record ist jetzt spezifiziert in
> [`channel_rendezvous_dht.md`](../channel_rendezvous_dht.md) — QR v4 (`{v, label, channel_sk}`),
> Record-Key `H(dateUTC ‖ recordPubkey)` mit eigenem Domain-Tag, mit `k_enc` verschlüsselter opaker
> Wert (Teilnehmer + je Teilnehmer OH-Liste), Channel-Signatur, feste Padding-Bucket-Größe, TTL 48 h,
> Zugriff für Light Clients garlic-gewrappt via `record_store` / `record_lookup`.

---

## 7) ACK & Push (im Kontext OH)

Wenn die Garlic Message am OH ankommt, öffnet der Outbound Server die **letzte Schicht** und findet darin:

- **Push-Nachricht (`push`)**: das eigentliche Payload, das zugestellt/weitergeleitet werden soll.
- **ACK-Block (`ack`)**: Quittung/Bestätigung (Semantik noch festzulegen: Delivery vs. Read vs. „stored“).

Beide werden aus **der finalen Garlic-Schicht** extrahiert.

---

## 8) Beispiel-Skalierung (Gruppenchat)

Beispiel:
- Alice’ Light Client registriert **3 Outbound Handles** (auf 3 Outbound Servern).
- Alice hat **2 Channels** und teilt **alle 3 OH** in **beide** Channels.
- Ein Channel mit vielen Teilnehmern: **pro Teilnehmer 3 OH**.

Gruppenchat-Beispiel:
- 20 Teilnehmer → **60 OH** im Channel.
- Jede Zustellung/ACK über **3 Hops** zum Outbound Server.

**Folge:**  
CPU/Krypto auf Smartphones ist meist nicht der Engpass; häufiger limitieren **Traffic, Latenz und viele kleine Netztransfers** (Radio-Wakeups), insbesondere bei hoher Nachrichtenfrequenz.

---

## 9) Nicht übersetzen
Der Begriff **„Flaschenpost“** wird im Projekt **nicht übersetzt**.

