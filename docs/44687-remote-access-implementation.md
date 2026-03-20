# 44687 - Remote Device Access: Implementation Document & Stories

> Source: Confluence — Software Ecosystems Hub & NEON / 44687 - Remote Device Access revisited

---

## Table of Contents

1. [Overview](#overview)
2. [Glossary](#glossary)
3. [Architecture Summary](#architecture-summary)
4. [Flow 1 — Session Establishment](#flow-1--session-establishment)
5. [Flow 2 — Session Consolidation](#flow-2--session-consolidation)
6. [Flow 3 — Session Established (Monitoring)](#flow-3--session-established-monitoring)
7. [Flow 4 — Session Termination by Request](#flow-4--session-termination-by-request)
8. [Flow 5 — Session Termination by Timeout](#flow-5--session-termination-by-timeout)
9. [Flow 6 — Session Termination by Device Connection Loss](#flow-6--session-termination-by-device-connection-loss)
10. [Communication Interfaces](#communication-interfaces)
11. [Implementation Stories](#implementation-stories)

---

## Overview

This feature enables users to remotely connect to services running on IoT devices (SSH, VNC, web servers, etc.) through a secure, E2E-encrypted tunnel built on **WireGuard** and **wstunnel** over **WebSocket (wss)**. It replaces the previous SSM-based approach that had a hard 3 Mbps bandwidth limit.

### Key Properties

- Bandwidth is offloaded to the physical medium (no software-imposed cap; POC achieved 22–30 Mbps)
- Sessions are isolated by design — each session is a separate podman pod
- Multiple devices can be grouped under the same virtual network
- Jumpboxes are always-on EC2 instances for hot-start in geographically close regions

### Connection Modes

| Mode | Format | Use Case |
|---|---|---|
| SSH Port Forwarding | `ssh -i cert -L <port>:<device-uuid>:<target-port> user@jumpbox -p <random-port>` | Low-bandwidth (SSH, SCP) |
| WebSocket | `wss://<jumpbox>/<device-UUID>/<session-UUID>/<tcp-port>` | Browser-based or high-bandwidth (VNC) |
| Full WireGuard Config | User receives a WireGuard config file | Full VPN access to the device network |

---

## Glossary

| Abbreviation | Full Name | Description |
|---|---|---|
| **BE** | Xalt IoT Cloud Back End | Microservices and user-facing application; coordinator of information to/from devices |
| **Edge** | Xalt IoT Edge Agent | SDK installed on devices for communication with BE (telemetry, functions, remote access) |
| **Infra** | Xalt IoT Cloud Infrastructure | Backbone of BE; provisions EC2 instances, IoT Core, backplane functions |
| **CP** | Control Plane | Software on Infra that allows BE to use remote access functionality |
| **FE** | Xalt IoT Cloud Front End | User interface (Xalt Studio) for customers/users/tenants |
| **RA** | Remote Access | The feature this document describes |
| **Session** | Session | A podman pod containing: wstunnel, WireGuard server, WireGuard client, SSH tunnel port forward, websocketify |

---

## Architecture Summary

### Infrastructure

- **Jumpbox**: Always-on EC2 instance per region
- **Initial Regions**: `us-east-1`, `eu-central-1`, `ap-southeast-2` (V1: `eu-west-1` only)
- **WireGuard Address Space**: `10.x.x.x/22` — ~16K subnets, max 1024 devices per session
- **Geolocation**: Rough geoposition of device used to select the closest jumpbox

### Session Components (Podman Pod)

Each session consists of the following containers:

1. **wstunnel service** — WebSocket tunnelling
2. **WireGuard server** — VPN endpoint
3. **WireGuard client** — VPN client connector
4. **SSH tunnel port forward** — Port forwarding via SSH
5. **websocketify** — WebSocket-to-TCP bridge

---

## Flow 1 — Session Establishment

**Trigger**: User/client requests a new remote access session via REST API.

### Sequence

```
User/FE ──► BE ──► CP (create session) ──► returns SessionID, DNS, WS naming
                ──► Edge/Device (MQTT init) ──► Device generates WireGuard keypair
Device ──► BE (MQTT: init/accepted + public key)
BE ──► CP (add device to session with public key + sessionUUID)
CP ──► BE (Kafka: WireGuard client config, wstunnel config, WG server status)
BE ──► Device (MQTT: configure_and_connect command with all params)
Device ──► starts WireGuard + wstunnel ──► BE (MQTT: processes_started)
```

### Step-by-Step

1. **Client calls BE** — `POST /api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/start`
2. **BE contacts CP** — Requests a new session for a specific region with:
   - Port(s) to be mapped
   - WebSocket activation (Caddy/Nginx for multi-port)
   - QoS requirements (future)
3. **BE sends MQTT `init` to device** — Topic: `xiot/devices/<deviceId>/remoteaccess/init`
4. **CP returns session details** (via Kafka):
   - `SessionID`
   - WebSocket naming for end-user side
   - DNS address of the session
5. **Device responds with public key** — Topic: `xiot/devices/<deviceId>/remoteaccess/init/accepted`
   - Kafka event: `REMOTE_ACCESS_INIT_ACCEPTED_EVENT` with `{ deviceId, wireguardPublicKey }`
6. **BE asks CP to add device to session** with:
   - Device's WireGuard public key
   - `sessionUUID`
7. **CP returns configuration** (via Kafka):
   - WireGuard client config (includes device IP inside VPN)
   - wstunnel configuration
   - WireGuard server status
8. **BE sends MQTT `configure and connect`** to device — Topic: `xiot/devices/<deviceId>/remoteaccess/start`
   - Payload includes: IP address, wstunnel address, WireGuard server address/port, wstunnel port, keepalive value
9. **Device starts processes** and reports back — Topic: `xiot/devices/<deviceId>/remoteaccess/start/accepted`
   - Kafka event: `REMOTE_ACCESS_START_ACCEPTED_EVENT`
10. **Failover/retry on timeout** if device does not respond

> **Note**: The WireGuard configuration inherently contains the IP addresses of both the device and the client.

---

## Flow 2 — Session Consolidation

**Trigger**: Device reports WireGuard processes started (MQTT `start/accepted`).

### Sequence

```
Device ──► BE (MQTT: processes started / start/accepted)
BE ──► CP (request status for device/session)
CP ──► BE (keepalive info of clients)
BE ──► User/FE (session state → STARTED)
```

### Step-by-Step

1. **Device sends `start/accepted`** via MQTT — WireGuard and wstunnel are running
2. **BE asks CP to confirm** device has joined the network: request status for device/session
3. **CP returns keepalive information** of the connected clients
4. **BE updates session state to `STARTED`** and returns information to the user
5. **Timeout**: If the device does not appear within **1 minute**, the initialisation is considered **failed**

---

## Flow 3 — Session Established (Monitoring)

**Trigger**: Session reaches `STARTED` state.

### Behaviour

1. **BE starts monitoring** the session
2. **BE polls CP every 30 seconds** for alive status
3. **CP executes `wg show`** on the WireGuard server
4. **BE returns to user** a list of devices in the session with:
   - Device UUID
   - IP address (within VPN)
   - Last keepalive time
   - Keepalive max value

### Session Details API

`GET /api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/{sessionId}`

Returns:
- Session status (`PROVISIONING`, `STARTED`, `STOPPING`, `STOPPED`, `FAILED`)
- Device connectivity status per device
- SSH certificate details (if SSH port forwarding was requested)
- WireGuard config (if WireGuard VPN was requested)
- WebSocket endpoints (if WebSocket ports were requested)

---

## Flow 4 — Session Termination by Request

**Trigger**: User explicitly requests session termination.

### Sequence

```
User/FE ──► BE (stop session request)
BE ──► Device (MQTT: stop command)
Device ──► BE (MQTT: stop/accepted)
BE ──► CP (terminate session + all containers)
CP ──► cleanup
```

### Step-by-Step

1. **User requests stop** — `POST /api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/{sessionId}/stop`
2. **BE sends MQTT `stop`** to device — Topic: `xiot/devices/<deviceId>/remoteaccess/stop`
3. **Device acknowledges** — Topic: `xiot/devices/<deviceId>/remoteaccess/stop/accepted`
   - Kafka event: `REMOTE_ACCESS_STOP_ACCEPTED_EVENT`
4. **BE contacts CP** to terminate the session and destroy all containers
5. **CP performs cleanup** — removes podman pod, WireGuard configs, certificates

---

## Flow 5 — Session Termination by Timeout

**Trigger**: Maximum session duration (`deviceRemoteSessionMaxTtl`) is reached.

### Sequence

```
Timer expires ──► BE detects max session length
BE ──► Device (MQTT: stop command)
Device ──► BE (MQTT: stop/accepted)
BE ──► CP (terminate session + all containers)
CP ──► cleanup
```

### Step-by-Step

1. **BE detects timeout** — max session length reached (default: 2 hours, max: 6 months)
2. **BE sends disconnect command** to device via MQTT
3. **BE contacts CP** to terminate the session and all containers
4. **CP performs cleanup**

---

## Flow 6 — Session Termination by Device Connection Loss

**Trigger**: Device loses connectivity (stale connection detected).

### Behaviour

- BE's periodic monitoring (every 30 seconds via `wg show`) detects that keepalive has exceeded the max value
- Session is marked as stale/failed
- BE initiates cleanup via CP
- Containers and session resources are destroyed

---

## Communication Interfaces

### MQTT Topics (Edge <-> BE)

#### Outbound (BE -> Device)

```
xiot/devices/<deviceId>/remoteaccess/init
xiot/devices/<deviceId>/remoteaccess/start
xiot/devices/<deviceId>/remoteaccess/stop
```

#### Inbound (Device -> BE)

```
xiot/devices/<deviceId>/remoteaccess/init/accepted
xiot/devices/<deviceId>/remoteaccess/init/rejected
xiot/devices/<deviceId>/remoteaccess/start/accepted
xiot/devices/<deviceId>/remoteaccess/start/rejected
xiot/devices/<deviceId>/remoteaccess/stop/accepted
xiot/devices/<deviceId>/remoteaccess/stop/rejected
```

All inbound messages are routed via **IoT Core** to Kafka topic: `xiot.device.remoteaccess`

#### IoT Core Routing Rule

| Rule | Source Topics | Kafka Destination | Message Type Header |
|---|---|---|---|
| `remoteAccessActions` | `xiot/devices/{deviceId}/remoteaccess/{action}/{status}` | `xiot.device.remoteaccess` | `REMOTE_ACCESS_DEVICE_EVENT` |

Kafka headers: `deviceId`, `receivedAt`, `traceId`, `principal`, `sourceIp`, `xiot_action`, `xiot_actionStatus`, `xiot_messageType`

### Kafka Topics (BE <-> Infra/CP)

```
# Init
xiot.device.remoteaccess.init.success.json
xiot.device.remoteaccess.init.failed.json

# Start
xiot.device.remoteaccess.start.success.json
xiot.device.remoteaccess.start.failed.json

# Stop
xiot.device.remoteaccess.stop.success.json
xiot.device.remoteaccess.stop.failed.json

# Session config from CP
xiot.device.remoteaccess.session.config.json
xiot.device.remoteaccess.session.config.json.dlt

# Dead Letter Topic
xiot.device.remoteaccess.session.json.dlt
```

### Internal MQTT/Kafka Event Payloads

```json
// REMOTE_ACCESS_INIT_ACCEPTED_EVENT
{ "deviceId": "<deviceId>", "wireguardPublicKey": "<key>" }

// REMOTE_ACCESS_INIT_REJECTED_EVENT
{ "deviceId": "<deviceId>", "error": "<int>", "message": "<string>" }

// REMOTE_ACCESS_START_ACCEPTED_EVENT
{ "deviceId": "<deviceId>" }

// REMOTE_ACCESS_START_REJECTED_EVENT
{ "deviceId": "<deviceId>", "error": "<int>", "message": "<string>" }

// REMOTE_ACCESS_STOP_ACCEPTED_EVENT
{ "deviceId": "<deviceId>", "message": "<string>" }

// REMOTE_ACCESS_STOP_REJECTED_EVENT
{ "deviceId": "<deviceId>", "error": "<int>", "message": "<string>" }
```

### REST API (BE <-> FE / External Clients)

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/devices` | GET | List devices with remote access capability info |
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}` | PUT | Edit device group (set `deviceRemoteSessionMaxTtl`) |
| `/api/v1/tenant/{tenantId}/session/regions` | GET | Get supported regions for remote access |
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/start` | POST | Start a remote access session |
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/{sessionId}` | GET | Get session details |
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/{sessionId}/stop` | POST | Stop a remote access session |
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/{sessionId}/devices` | POST | Add devices to existing session |
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/{sessionId}/devices` | DELETE | Remove devices from session |
| `/api/v1/tenant/{tenantId}/device-group/{deviceGroupId}/remote-access/session/{sessionId}/extend` | POST | Extend session duration |

---

## Implementation Stories

### Epic: Remote Access Session Lifecycle

---

### Story 1: Session Creation — BE REST API

**As a** user/external client,
**I want to** start a remote access session via REST API,
**so that** I can initiate a secure connection to one or more devices.

#### Acceptance Criteria

- [ ] `POST /api/v1/tenant/{tenantId}/device-group/{dgId}/remote-access/session/start` endpoint is implemented
- [ ] Accepts `sessionDuration`, `sessionCertificateMaxTtl`, `region`, `wgAddressSpace`, `devices[]`, `wireGuard.exposed`
- [ ] Validates all devices are onboarded, online, and remote-access ready (`remoteAccess.available == true`)
- [ ] Validates `sessionDuration` does not exceed `deviceRemoteSessionMaxTtl` (default: 2h, max: 6 months)
- [ ] Validates `sessionCertificateMaxTtl` does not exceed `sessionDuration`
- [ ] Defaults `region` to `eu-west-1` if not provided
- [ ] Returns `202 Accepted` with `sessionId` on success
- [ ] Returns appropriate error codes (400, 403, 404, 409, 500)

**Story Points**: 8

---

### Story 2: Session Creation — BE -> CP Communication

**As a** backend service,
**I want to** request the control plane to provision a new session,
**so that** the jumpbox infrastructure is ready for device connections.

#### Acceptance Criteria

- [ ] BE sends session creation request to CP via Kafka with region, port mappings, and WebSocket config
- [ ] BE consumes CP response from `xiot.device.remoteaccess.session.config.json`
- [ ] Receives and stores: `SessionID`, WebSocket naming, DNS address
- [ ] Handles failures from `xiot.device.remoteaccess.session.config.json.dlt`
- [ ] Session state set to `PROVISIONING`

**Story Points**: 5

---

### Story 3: Session Creation — BE -> Edge MQTT Init

**As a** backend service,
**I want to** send an init command to the device via MQTT,
**so that** the device generates its WireGuard keypair.

#### Acceptance Criteria

- [ ] BE publishes to `xiot/devices/<deviceId>/remoteaccess/init`
- [ ] BE consumes `REMOTE_ACCESS_INIT_ACCEPTED_EVENT` from Kafka (`xiot.device.remoteaccess`) containing `wireguardPublicKey`
- [ ] BE consumes `REMOTE_ACCESS_INIT_REJECTED_EVENT` and updates session status with error details
- [ ] Timeout handling: if no response within configurable period, mark device init as failed

**Story Points**: 5

---

### Story 4: Session Creation — Add Device to Session (BE -> CP)

**As a** backend service,
**I want to** register the device's public key with the control plane,
**so that** the CP can generate WireGuard and wstunnel configurations.

#### Acceptance Criteria

- [ ] BE sends device public key + sessionUUID to CP via Kafka
- [ ] CP returns: WireGuard client config (with device VPN IP), wstunnel config, WG server status
- [ ] BE consumes response from `xiot.device.remoteaccess.start.success.json`
- [ ] BE handles failures from `xiot.device.remoteaccess.start.failed.json`

**Story Points**: 5

---

### Story 5: Session Creation — Configure and Connect (BE -> Edge)

**As a** backend service,
**I want to** send the connection configuration to the device via MQTT,
**so that** the device can start WireGuard and wstunnel processes.

#### Acceptance Criteria

- [ ] BE publishes `configure_and_connect` command to `xiot/devices/<deviceId>/remoteaccess/start`
- [ ] Payload includes: IP address, wstunnel address, WireGuard server address/port, wstunnel port, keepalive value
- [ ] BE waits for `REMOTE_ACCESS_START_ACCEPTED_EVENT` from device
- [ ] Implements retry/failover on timeout

**Story Points**: 5

---

### Story 6: Session Consolidation

**As a** backend service,
**I want to** verify the device has successfully joined the WireGuard network,
**so that** I can transition the session to STARTED and notify the user.

#### Acceptance Criteria

- [ ] On receiving `start/accepted` from device, BE requests device/session status from CP
- [ ] CP returns keepalive information of connected clients
- [ ] Session state transitions to `STARTED`
- [ ] If device does not appear within **1 minute**, session is marked as `FAILED`
- [ ] User/FE is notified of final session state

**Story Points**: 5

---

### Story 7: Session Monitoring

**As a** backend service,
**I want to** continuously monitor active sessions,
**so that** I can detect connection loss and report session health.

#### Acceptance Criteria

- [ ] BE polls CP every **30 seconds** for each active session
- [ ] CP executes `wg show` and returns per-device status
- [ ] BE exposes per-device info: UUID, IP address, last keepalive time, keepalive max value
- [ ] Session details API returns current monitoring data
- [ ] Stale connection detection triggers Flow 6

**Story Points**: 8

---

### Story 8: Session Details API

**As a** user/external client,
**I want to** query session details,
**so that** I can see connection status, device info, and credentials.

#### Acceptance Criteria

- [ ] `GET /api/v1/tenant/{tenantId}/device-group/{dgId}/remote-access/session/{sessionId}` endpoint is implemented
- [ ] Returns session status: `PROVISIONING`, `STARTED`, `STOPPING`, `STOPPED`, `FAILED`
- [ ] Returns per-device connectivity status
- [ ] Returns SSH certificate details (if SSH port forwarding was requested)
- [ ] Returns WireGuard config (if `wireGuard.exposed == true`)
- [ ] Returns WebSocket endpoints per device/port
- [ ] Returns appropriate error codes (400, 403, 404, 500)

**Story Points**: 5

---

### Story 9: Session Termination by User Request

**As a** user/external client,
**I want to** stop an active remote access session,
**so that** resources are released and the connection is securely closed.

#### Acceptance Criteria

- [ ] `POST .../session/{sessionId}/stop` endpoint is implemented
- [ ] BE sends MQTT `stop` to all devices in session: `xiot/devices/<deviceId>/remoteaccess/stop`
- [ ] BE consumes `REMOTE_ACCESS_STOP_ACCEPTED_EVENT` from each device
- [ ] BE sends session termination request to CP
- [ ] CP destroys podman pod (wstunnel, WireGuard server/client, SSH tunnel, websocketify)
- [ ] Session state transitions to `STOPPING` -> `STOPPED`
- [ ] Handles `REMOTE_ACCESS_STOP_REJECTED_EVENT` gracefully

**Story Points**: 5

---

### Story 10: Session Termination by Timeout

**As a** backend service,
**I want to** automatically terminate sessions that exceed their maximum duration,
**so that** resources are not held indefinitely.

#### Acceptance Criteria

- [ ] BE tracks session start time and `deviceRemoteSessionMaxTtl`
- [ ] When max duration is reached, BE initiates the same termination flow as Story 9
- [ ] Sends MQTT disconnect to all devices
- [ ] Contacts CP to terminate session and containers
- [ ] Session state transitions to `STOPPED`
- [ ] Audit event is recorded with reason `TIMEOUT`

**Story Points**: 5

---

### Story 11: Session Termination by Connection Loss

**As a** backend service,
**I want to** detect and handle stale device connections,
**so that** orphaned sessions are cleaned up automatically.

#### Acceptance Criteria

- [ ] Monitoring (Story 7) detects keepalive exceeds max value
- [ ] Session is marked as `FAILED` with reason `CONNECTION_LOSS`
- [ ] BE initiates cleanup via CP (destroy containers)
- [ ] If device reconnects after cleanup, it is rejected
- [ ] Audit event is recorded

**Story Points**: 5

---

### Story 12: Add Devices to Existing Session

**As a** user/external client,
**I want to** add new devices to a running session,
**so that** I can extend remote access without creating a new session.

#### Acceptance Criteria

- [ ] `POST .../session/{sessionId}/devices` endpoint is implemented
- [ ] New devices must be onboarded, online, and remote-access ready
- [ ] Follows the same init -> configure_and_connect -> consolidation flow per device
- [ ] Session remains in `STARTED` state for existing devices
- [ ] New devices appear in session details after consolidation

**Story Points**: 8

---

### Story 13: Remove Devices from Session

**As a** user/external client,
**I want to** remove specific devices from a session,
**so that** I can manage the session without terminating it entirely.

#### Acceptance Criteria

- [ ] `DELETE .../session/{sessionId}/devices` endpoint is implemented
- [ ] BE sends MQTT `stop` only to removed devices
- [ ] BE requests CP to remove device's WireGuard peer from the session
- [ ] Session continues for remaining devices
- [ ] If last device is removed, session is terminated

**Story Points**: 5

---

### Story 14: Extend Session Duration

**As a** user/external client,
**I want to** extend the duration of an active session,
**so that** I can continue working without session interruption.

#### Acceptance Criteria

- [ ] `POST .../session/{sessionId}/extend` endpoint is implemented
- [ ] New total duration must not exceed `deviceRemoteSessionMaxTtl`
- [ ] Session timeout timer is reset
- [ ] Certificates are renewed if necessary
- [ ] Returns updated session details

**Story Points**: 5

---

### Story 15: Get Supported Regions

**As a** user/external client,
**I want to** query available remote access regions,
**so that** I can select the closest region for my session.

#### Acceptance Criteria

- [ ] `GET /api/v1/tenant/{tenantId}/session/regions` endpoint is implemented
- [ ] Returns list of regions with `code` and `name`
- [ ] V1 returns only `eu-west-1`
- [ ] Future regions can be added without breaking changes

**Story Points**: 2

---

### Story 16: Device Group Remote Access Configuration

**As an** administrator,
**I want to** configure `deviceRemoteSessionMaxTtl` on a device group,
**so that** I can control the maximum session duration for all devices in the group.

#### Acceptance Criteria

- [ ] `PUT /api/v1/tenant/{tenantId}/device-group/{dgId}` accepts `deviceRemoteSessionMaxTtl` (ISO-8601 duration)
- [ ] Default: 2 hours; Maximum: 6 months
- [ ] Values exceeding 6 months are rejected
- [ ] Existing device groups without this field continue to use the default
- [ ] Backward compatible — no breaking changes

**Story Points**: 3

---

### Story 17: Device Remote Access Capability Discovery

**As a** user/external client,
**I want to** see which devices support remote access,
**so that** I only attempt sessions with capable devices.

#### Acceptance Criteria

- [ ] `GET .../devices` endpoint returns `remoteAccess` object per device
- [ ] `remoteAccess.available` is the authoritative flag
- [ ] Shows WireGuard support + version
- [ ] Shows WebSocket tunnel support + version
- [ ] Devices without WireGuard or wstunnel installed show `available: false`

**Story Points**: 3

---

### Story 18: IoT Core Routing Rules for Remote Access

**As an** infrastructure engineer,
**I want to** configure IoT Core routing rules for remote access MQTT topics,
**so that** device messages are correctly forwarded to Kafka.

#### Acceptance Criteria

- [ ] Rule `remoteAccessActions` routes `xiot/devices/{deviceId}/remoteaccess/{action}/{status}` to Kafka topic `xiot.device.remoteaccess`
- [ ] Kafka headers include: `deviceId`, `receivedAt`, `traceId`, `principal`, `sourceIp`, `xiot_action`, `xiot_actionStatus`, `xiot_messageType`
- [ ] `xiot-device-events-consumer` processes messages and routes to `xiot-remote-access-manager`

**Story Points**: 3

---

### Story 19: Kafka Topic Provisioning

**As an** infrastructure engineer,
**I want to** create all Kafka topics for remote access,
**so that** async communication between BE, CP, and Edge functions correctly.

#### Acceptance Criteria

- [ ] Topics created:
  - `xiot.device.remoteaccess.init.success.json`
  - `xiot.device.remoteaccess.init.failed.json`
  - `xiot.device.remoteaccess.start.success.json`
  - `xiot.device.remoteaccess.start.failed.json`
  - `xiot.device.remoteaccess.stop.success.json`
  - `xiot.device.remoteaccess.stop.failed.json`
  - `xiot.device.remoteaccess.session.config.json`
  - `xiot.device.remoteaccess.session.config.json.dlt`
  - `xiot.device.remoteaccess.session.json.dlt`
- [ ] Retention and partitioning configured per environment requirements

**Story Points**: 2

---

### Story 20: Edge Agent — Remote Access Service

**As an** edge agent,
**I want to** handle remote access MQTT commands (init, start, stop),
**so that** I can manage WireGuard and wstunnel lifecycle on the device.

#### Acceptance Criteria

- [ ] Subscribes to `xiot/devices/<deviceId>/remoteaccess/init`
- [ ] On `init`: generates WireGuard keypair, publishes public key to `init/accepted`
- [ ] Subscribes to `xiot/devices/<deviceId>/remoteaccess/start`
- [ ] On `start`: configures and starts WireGuard client + wstunnel with provided params, publishes `start/accepted`
- [ ] Subscribes to `xiot/devices/<deviceId>/remoteaccess/stop`
- [ ] On `stop`: tears down WireGuard + wstunnel, publishes `stop/accepted`
- [ ] Reports errors to `*/rejected` topics with error code and message
- [ ] Reports WireGuard and wstunnel versions via inventory

**Story Points**: 13

---

### Story 21: Control Plane — Session Provisioning

**As a** control plane service,
**I want to** provision and manage remote access sessions on the jumpbox,
**so that** the infrastructure is ready for device connections.

#### Acceptance Criteria

- [ ] Creates podman pod with: wstunnel, WireGuard server, WireGuard client, SSH tunnel, websocketify
- [ ] Allocates WireGuard subnet from `10.x.x.x/22` address space
- [ ] Generates WireGuard server keypair and config
- [ ] Adds device peers with their public keys
- [ ] Configures Caddy/Nginx for WebSocket port exposure
- [ ] Returns session config via Kafka
- [ ] Supports session teardown (destroy pod + cleanup)
- [ ] Exposes `wg show` status for monitoring

**Story Points**: 21

---

### Story 22: Audit & Observability

**As an** operator,
**I want to** track session audit data and observe session health,
**so that** I can troubleshoot issues and meet compliance requirements.

#### Acceptance Criteria

- [ ] Track per session: user, start date, end date, duration, termination reason
- [ ] Track per session: data in/out (from WireGuard ethernet device stats)
- [ ] Track per device: data in/out per session
- [ ] Session QoS metrics (when possible)
- [ ] Meta info: region, device geolocation
- [ ] Micrometer metrics exposed
- [ ] Grafana dashboard for session monitoring

**Story Points**: 8

---

## Story Point Summary

| Area | Stories | Total SP |
|---|---|---|
| BE — REST API | Stories 1, 8, 9, 12, 13, 14, 15, 16, 17 | 44 |
| BE — Orchestration | Stories 2, 3, 4, 5, 6, 7, 10, 11 | 43 |
| Infrastructure | Stories 18, 19 | 5 |
| Edge | Story 20 | 13 |
| Control Plane | Story 21 | 21 |
| Observability | Story 22 | 8 |
| **Total** | **22 stories** | **134 SP** |

---

## Risk Register

| Risk | Mitigation |
|---|---|
| Session setup time > 5 seconds | Keep warm connections; pre-configure WireGuard; fast podman startup |
| WireGuard/wstunnel version misalignment | Report versions in device inventory; spawn compatible server version if needed |
| Device connectivity loss during setup | 1-minute timeout with FAILED state; retry mechanism |
| Subnet collision (1/16634 probability) | Track allocated subnets; future: dynamic subnet sizing based on expected device count |
