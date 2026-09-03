# Wire Command & Proto Registry

> Registry of every byte that acts as a command on the Redpanda wire, plus the protobuf
> definitions carried inside those frames.
>
> **`redpandaj` is the source of truth.** The table block at the end of this document is
> generated inside the `redpandaj` repository from `im.redpanda.core.Command`,
> `im.redpanda.flaschenpost.FlaschenpostV2` and `src/main/proto/*.proto`, and a JUnit test there
> fails the build when code and registry diverge. Do not edit the generated block by hand —
> see [Regenerating](#regenerating).
>
> Introduced for the DDD architecture review of 2026-08-31 (§6 P0, remediation (a) of
> `protocol-opus.md` §5: *"no wire-command registry in `docs`"*). Adding this document changed no
> wire semantics.

## Why this exists

Before this registry the protocol contract existed only as constants: the command bytes lived in
`Command.java`, the garlic layer commands in `FlaschenpostV2.java`, and the light client mirrored
both by hand in Dart — two of them as bare integer literals. There was no place where the whole
byte space could be read at once, and nothing that noticed when one side moved. The registry does
not remove the mirrors, but it gives them a single checkable reference.

## How to read the tables

There are two distinct command spaces, and a byte value means nothing without knowing which one it
belongs to:

* **Top-level commands** (`Command.java`) are the first byte of a frame on a peer connection.
  Payload-carrying commands are framed `[cmd][len:4][payload]`; handshake and liveness commands
  carry a fixed-size or empty payload. After `ACTIVATE_ENCRYPTION` the same framing continues
  inside the AES-GCM stream.
* **Garlic layer commands** (`FlaschenpostV2.java`) are the first byte of the *decrypted plaintext
  of one garlic layer*, i.e. they only ever appear inside a `FLASCHENPOST_V2` (142) packet. Their
  values `0x01`–`0x06` deliberately overlap with top-level values and are unrelated to them.

Gaps in the top-level range are historical: `4` was never assigned, and `17`–`119`, `123`–`129`,
`131`–`140`, `143`–`149` are free. Unknown commands are **not** safely skippable by existing light
clients (the Dart read loop discards a single byte and then desyncs), so a new command may only be
sent to a peer that has announced it understands it — this is why `OUTBOUND_NOTIFY` is opt-in
behind `OUTBOUND_SUBSCRIBE_REQ`.

### Top-level command groups

| Range | Meaning |
| --- | --- |
| 1–3 | Handshake: node id exchange (`REQUEST_PUBLIC_KEY` / `SEND_PUBLIC_KEY`) and the switch to the encrypted framed stream (`ACTIVATE_ENCRYPTION`). |
| 5–6 | Liveness (`PING` / `PONG`). |
| 7–8 | Peer list exchange (`RequestPeerList` / `SendPeerList`). |
| 9–12 | P2P auto-updater for the node jar: timestamp and content request/answer. |
| 13–16 | Same updater protocol for the Android artifact. |
| 120–122 | Kademlia DHT: store, get, get-answer. |
| 130 | `JOB_ACK` — acknowledges a DHT job. |
| 141 / 158 | MS02 mailbox deposit (`FLASCHENPOST_PUT`) and its response. |
| 142 | MS04 fixed-size (2048 byte) multi-hop garlic packet — carries the garlic layer commands below. |
| 150–157, 159–161 | Outbound Handle (OH) service: register, fetch, revoke, ack-fetch (MS02b/MS06) and the Connection-Notify subscribe/notify pair (T38). |

### Garlic layer commands

| Command | Meaning |
| --- | --- |
| `CMD_FORWARD` (0x01) | Relay: peel one layer and rebuild a fresh 2048-byte packet for the next hop. |
| `CMD_DELIVER` (0x02) | Final hop: deposit the payload into the local OH mailbox. |
| `CMD_DELIVER_TAGGED` (0x03) | MS05 reverse garlic: `CMD_DELIVER` plus a 16-byte session tag stored on the mail item. |
| `CMD_DELIVER_ACKED` (0x04) | MS06: deliver and send a `RoutingAck` back along the contained return path. |
| `CMD_RECORD_STORE` (0x05) | T43 channel rendezvous: store a signed record in the DHT (see `channel_rendezvous_dht.md`). |
| `CMD_RECORD_LOOKUP` (0x06) | T43 channel rendezvous: look a record up and answer via the return path. |

### Proto files

| File | Content |
| --- | --- |
| `commands.proto` | Node-to-node payloads of the original protocol: peer list entries, the Kademlia DHT messages, `JobAck`, the MS02 `FlaschenpostPut` deposit and the `PandaMessage` envelope. |
| `outbound.proto` | Outbound Handle service v1: register/fetch/ack-fetch/revoke/subscribe/notify request-response pairs, the `MailItem` payload, `RoutingAck`, the `OhNodeRecord` used for OH redundancy, and the shared `Status` enum. |

## Known drift sources

The registry is checked against redpandaj only. Everything below mirrors these values by hand and
is **not** covered by the check:

| Where | What |
| --- | --- |
| `redpanda-mobile` `packages/redpanda_light_client/lib/src/network/active_peer.dart` | Private `_cmd*` constants mirroring only the subset of top-level commands the read loop dispatches on — `10`–`12`, `14`–`16`, `154`–`156` and `159` have no constant here. |
| `redpanda-mobile` `packages/redpanda_light_client/lib/src/client/redpanda_light_client.dart` | `156` (`OUTBOUND_ACK_FETCH_REQ`) and `159` (`OUTBOUND_SUBSCRIBE_REQ`) appear as bare integer literals in the signing-byte builders and the send calls. |
| `redpanda-mobile` `packages/redpanda_light_client/lib/src/garlic/garlic_builder.dart` | `cmdForward`…`cmdRecordLookup` mirror the garlic layer commands. |
| `redpanda-mobile` `packages/redpanda_light_client/protos/commands.proto` | A stale copy of the redpandaj proto (task **T107** replaces it with a vendored, CI-checked copy). |

Closing the Dart side is deliberately out of scope here: T107 makes the redpandaj protos the single
proto source for the Dart client; the command-byte mirrors stay hand-maintained until a follow-up
task addresses them.

## Regenerating

In a `redpandaj` checkout:

```bash
mvn -q compile
java -cp target/classes im.redpanda.core.WireRegistry
```

This rewrites `src/main/resources/wire-registry.md` (in that checkout). Copy the resulting file
verbatim into the generated block below (between the `BEGIN`/`END` markers) and commit both
repositories.

`im.redpanda.core.WireRegistryTest` compares the checked-in file against the code on every
`mvn test`, so a changed command byte, a renamed constant, a new command or a new/renamed proto
definition turns the redpandaj build red until the registry is regenerated.

## Generated registry

<!-- BEGIN GENERATED BLOCK — verbatim copy of redpandaj/src/main/resources/wire-registry.md -->
<!-- Redpanda wire registry - GENERATED FILE, do not edit by hand.
     Sources: im.redpanda.core.Command, im.redpanda.flaschenpost.FlaschenpostV2,
     src/main/proto/*.proto
     Regenerate: mvn -q compile && java -cp target/classes im.redpanda.core.WireRegistry
     Verified by: im.redpanda.core.WireRegistryTest -->

## Top-level commands (`im.redpanda.core.Command`)

First byte of every frame on a peer connection.

| Constant | Dec | Hex |
| --- | ---: | --- |
| `REQUEST_PUBLIC_KEY` | 1 | `0x01` |
| `SEND_PUBLIC_KEY` | 2 | `0x02` |
| `ACTIVATE_ENCRYPTION` | 3 | `0x03` |
| `PING` | 5 | `0x05` |
| `PONG` | 6 | `0x06` |
| `REQUEST_PEERLIST` | 7 | `0x07` |
| `SEND_PEERLIST` | 8 | `0x08` |
| `UPDATE_REQUEST_TIMESTAMP` | 9 | `0x09` |
| `UPDATE_ANSWER_TIMESTAMP` | 10 | `0x0A` |
| `UPDATE_REQUEST_CONTENT` | 11 | `0x0B` |
| `UPDATE_ANSWER_CONTENT` | 12 | `0x0C` |
| `ANDROID_UPDATE_REQUEST_TIMESTAMP` | 13 | `0x0D` |
| `ANDROID_UPDATE_ANSWER_TIMESTAMP` | 14 | `0x0E` |
| `ANDROID_UPDATE_REQUEST_CONTENT` | 15 | `0x0F` |
| `ANDROID_UPDATE_ANSWER_CONTENT` | 16 | `0x10` |
| `KADEMLIA_STORE` | 120 | `0x78` |
| `KADEMLIA_GET` | 121 | `0x79` |
| `KADEMLIA_GET_ANSWER` | 122 | `0x7A` |
| `JOB_ACK` | 130 | `0x82` |
| `FLASCHENPOST_PUT` | 141 | `0x8D` |
| `FLASCHENPOST_V2` | 142 | `0x8E` |
| `OUTBOUND_REGISTER_OH_REQ` | 150 | `0x96` |
| `OUTBOUND_REGISTER_OH_RES` | 151 | `0x97` |
| `OUTBOUND_FETCH_REQ` | 152 | `0x98` |
| `OUTBOUND_FETCH_RES` | 153 | `0x99` |
| `OUTBOUND_REVOKE_OH_REQ` | 154 | `0x9A` |
| `OUTBOUND_REVOKE_OH_RES` | 155 | `0x9B` |
| `OUTBOUND_ACK_FETCH_REQ` | 156 | `0x9C` |
| `OUTBOUND_ACK_FETCH_RES` | 157 | `0x9D` |
| `FLASCHENPOST_PUT_RES` | 158 | `0x9E` |
| `OUTBOUND_SUBSCRIBE_REQ` | 159 | `0x9F` |
| `OUTBOUND_SUBSCRIBE_RES` | 160 | `0xA0` |
| `OUTBOUND_NOTIFY` | 161 | `0xA1` |

## Garlic layer commands (`im.redpanda.flaschenpost.FlaschenpostV2`)

First byte of a decrypted garlic layer, inside a `FLASCHENPOST_V2` (142) packet.

| Constant | Dec | Hex |
| --- | ---: | --- |
| `CMD_FORWARD` | 1 | `0x01` |
| `CMD_DELIVER` | 2 | `0x02` |
| `CMD_DELIVER_TAGGED` | 3 | `0x03` |
| `CMD_DELIVER_ACKED` | 4 | `0x04` |
| `CMD_RECORD_STORE` | 5 | `0x05` |
| `CMD_RECORD_LOOKUP` | 6 | `0x06` |

## Protobuf definitions (`src/main/proto`)

| File | Kind | Name |
| --- | --- | --- |
| `commands.proto` | message | `KademliaIdProto` |
| `commands.proto` | message | `NodeIdProto` |
| `commands.proto` | message | `PeerInfoProto` |
| `commands.proto` | message | `SendPeerList` |
| `commands.proto` | message | `Ping` |
| `commands.proto` | message | `Pong` |
| `commands.proto` | message | `RequestPeerList` |
| `commands.proto` | message | `KademliaGet` |
| `commands.proto` | message | `KademliaGetAnswer` |
| `commands.proto` | message | `KademliaStore` |
| `commands.proto` | message | `JobAck` |
| `commands.proto` | message | `FlaschenpostPut` |
| `commands.proto` | message | `PandaMessage` |
| `outbound.proto` | enum | `Status` |
| `outbound.proto` | message | `RegisterOhRequest` |
| `outbound.proto` | message | `RegisterOhResponse` |
| `outbound.proto` | message | `FetchRequest` |
| `outbound.proto` | message | `MailItem` |
| `outbound.proto` | message | `FetchResponse` |
| `outbound.proto` | message | `AckFetchRequest` |
| `outbound.proto` | message | `AckFetchResponse` |
| `outbound.proto` | message | `FlaschenpostPutResponse` |
| `outbound.proto` | message | `OhNodeRecord` |
| `outbound.proto` | message | `RoutingAck` |
| `outbound.proto` | message | `RevokeOhRequest` |
| `outbound.proto` | message | `RevokeOhResponse` |
| `outbound.proto` | message | `SubscribeRequest` |
| `outbound.proto` | message | `SubscribeResponse` |
| `outbound.proto` | message | `Notify` |
<!-- END GENERATED BLOCK -->
