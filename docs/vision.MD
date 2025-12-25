Vision für das Redpanda-Projekt

Redpanda wird das “WhatsApp-Gefühl” für echte Peer-to-Peer-Kommunikation: zuverlässig genug für Alltag, privat genug für hohe Ansprüche, leicht genug für mobile Geräte – ohne zentrale Server als Single Point of Failure oder Vertrauen.

Warum das Projekt existiert

Souveränität: Kommunikation soll auch funktionieren, wenn Plattformen ausfallen, zensieren oder Daten auswerten.

Privacy by design: Metadaten sind oft schlimmer als Inhalte – Redpanda minimiert beides.

Mobil & realistisch: Kein “Tunnel alle 10 Minuten neu” als Grundvoraussetzung; Redpanda muss mit schlechten Netzen, NAT, Sleep-States und Akku leben.

Das Zukunftsbild (1–2 Jahre)

Ein Nutzer installiert eine App, wählt einen Kontakt, schreibt eine Nachricht – und es funktioniert:

Online/Offline-Phasen werden überbrückt (Store-and-forward/“Flaschenpost”).

Inhalte sind Ende-zu-Ende verschlüsselt, Routen sind mehrschichtig verschleiert (Garlic-Layering).

Das Netzwerk skaliert organisch: mehr Peers = besser, nicht fragiler.

Kernversprechen

Zuverlässig unter schlechten Bedingungen
Zustellung trotz NAT, wechselnder IPs, Mobilfunklöchern, Schlafmodus.

Starke Anonymität als Option, nicht als Luxus
Mehrstufige Routen/Onion-ähnliche Pfade, Traffic-Analysis-Resilienz, plausible Deniability wo sinnvoll.

Entwicklerfreundlich & modular
Saubere Protokollschichten, testbare Komponenten, klare Threat-Model-Dokumentation.

Leitprinzipien

“Mobile first”: Energie, Speicher, Datenverbrauch sind First-Class-Constraints.

“Fail soft”: Wenn ein Hop ausfällt, bricht nicht alles – es wird umgeroutet/weitergereicht.

“Minimal Trust”: Kein Peer muss “gut” sein, damit das System sicher bleibt.

“Observability ohne Leaks”: Diagnostik ja, aber ohne Privacy zu opfern.

Zielgruppe

Privacy-bewusste Nutzer, Communities, Aktivisten, Journalisten, Teams mit hohen Sicherheitsanforderungen.

Entwickler, die eine P2P-Messaging-Schicht als Baustein nutzen wollen (SDK/Lib).

Abgrenzung (bewusst nicht)

Kein “noch ein Chat nur für Nerds”, der im Alltag nicht deliver’t.

Kein reines Forschungsprojekt ohne Produktpfad.

Keine harte Abhängigkeit von dauerhaften Tunneln oder zentralen Relays.

Was “Erfolg” messbar bedeutet

Delivery-Rate bei realistischen Mobil-Szenarien (Sleep/NAT/Wechselnetz) ist hoch.

Median Time-to-Deliver bleibt akzeptabel, auch bei partieller Netzpartition.

Threat-Model & Security-Review sind nachvollziehbar; Angriffsflächen werden aktiv reduziert.

Onboarding: Setup unter 2 Minuten, ohne “lies erst dieses Paper”.

Der Elevator Pitch

Redpanda ist ein P2P-Messaging-Netzwerk, das Offline-Toleranz (Flaschenpost) mit mehrschichtiger Anonymität (Garlic) kombiniert – gebaut für die Realität mobiler Netze, nicht für ideale Labore.