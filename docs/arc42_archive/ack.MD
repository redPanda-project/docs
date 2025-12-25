# Ergebnis: ACK-Modell (Routing-Layer vs. Channel-Layer)

## Ziel
- **Outbound Nodes fair profilieren/scoren**, ohne Client-/OS-/Push-Effekte (offline, Battery-Saver, Throttling) mit Outbound-Qualität zu verwechseln.
- Deshalb: **Routing-ACKs getrennt von Channel-ACKs**.

---

## Begriffe
- **Light Client**: mobiles Endgerät; empfängt Nachrichten direkt (online) oder via Push (z. B. FCM/APNs).
- **Outbound Node**: Node, der Nachrichten **an Light Clients zustellt** (direkt oder via Push).
- **Garlic Message**: verschachtelte Nachricht, die beim **Outbound** final entschlüsselt wird.
- **Flaschenpost**: Store/Forward Paket über mehrere Hops (hier: ACK-Rückweg).

---

## Grundprinzip
- **ACKs im Routing-Layer entstehen am Outbound** (wichtigste Metrik fürs Outbound-Profiling).
- **ACKs werden vom Sender vorgefertigt (pre-crafted)** und in der Garlic Message mitgegeben.
- Der Outbound **erfindet keine ACK-Logik**, sondern **löst** die mitgelieferte ACK-Flaschenpost aus.

---

## Inhalt der Garlic Message (sichtbar nach Decrypt beim Outbound)
Beim Entschlüsseln der Garlic Message entstehen **zwei Outputs**:

1) **Delivery Payload**
   - verschlüsselter Content für den Light Client
   - Zustellhinweise (z. B. FCM/APNs Referenz/Key/Token-Handle, Direct-Session-Hint)

2) **ACK-Flaschenpost (Routing-ACK)**
   - vom Sender vorgefertigt
   - wird vom Outbound über **3 Hops** zurück geschickt

---

## ACK-Typen und Layer

### 1) Routing-Layer ACKs (für Outbound-Profiling)
Diese ACKs messen **Outbound-/Netz-Verhalten**, nicht den Client.

**R-ACK#1: OUTBOUND_RECEIVED / OUTBOUND_PROCESSED**
- Outbound hat Garlic entschlüsselt und den Delivery-Job übernommen/angestoßen.

**R-ACK#2: PUSH_ACCEPTED / PUSH_REJECTED (optional)**
- Nur wenn Push genutzt wird.
- Outbound hat an Push-Provider (FCM/APNs) übergeben und erhält unmittelbares Provider-Feedback:
  - **accepted** = Provider hat Anfrage angenommen (nicht gleich “Device hat’s gesehen”)
  - **rejected** = Provider lehnt ab (inkl. reason möglich)
- Wichtig: Push-Provider liefern out-of-the-box typischerweise **kein** verlässliches “Device hat wirklich empfangen/angezeigt” pro Message zurück.

**Regel für Scoring:** Outbound-Qualität basiert **nur** auf Routing-ACKs (R-ACKs).

---

### 2) Channel-Layer ACK (Client-ACK)
- Wird vom **Light Client** nach tatsächlichem Erhalt/Verarbeitung ausgelöst.
- Bedeutet: “Nachricht im Channel angekommen/akzeptiert/verarbeitet”.
- Kann stark verzögert sein (offline, OS-Limits, App nicht gestartet) und ist **nicht** geeignet, Outbound Nodes zu bewerten.

---

## Visualisierung (Flow)

### Hinweg
Sender -> Hop A -> Hop B -> Hop C -> Outbound -> (direkt) Client
                                      \-> (push) FCM/APNs -> Client

### Rückweg Routing-ACKs
- R-ACK#1: Outbound --Flaschenpost(3 hops)--> Sender
- R-ACK#2: Outbound --Flaschenpost(3 hops)--> Sender (nach Provider-Accept/Reject)

### Rückweg Channel-ACK
- Client --Channel-ACK--> zurück über Channel-Infrastruktur / eigenen Rückkanal

---

## Warum die Trennung wichtig ist
- **Channel-ACKs** hängen von Client/OS/Push-Zustellrealität ab (nicht Outbound-kontrollierbar).
- Für Outbound-Profiling darf man deshalb **Channel-ACKs nicht in Outbound-Scoring mischen**.
