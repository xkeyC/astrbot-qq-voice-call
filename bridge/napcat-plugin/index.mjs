import { spawn } from "node:child_process";
import { createHash, timingSafeEqual } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const MAX_EVENTS = 100;
const SENSITIVE_KEY =
  /(auth|ticket|token|sign|open_?key|d2|a2|cookie|session|credential|password|secret)/i;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);
const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
const MAX_WS_FRAME = 1024 * 1024;
// Call audio on the stream: 16-bit little-endian mono PCM at 48 kHz, both ways.
const PCM_ARGS = ["--raw", "--format=s16le", "--rate=48000", "--channels=1"];
const STREAM_TICK_MS = 100;
const MAX_AVSDK_LOGS = 50;
// An outgoing call nobody answers is closed after this long.
const DIAL_TIMEOUT_MS = 60000;
// AVSDK scene of a one-to-one call with a friend (QRTC SceneFriend).
const SCENE_FRIEND = 1;
// AVSDK outputs that end a call: peer rejected (20018), peer cancelled the
// invite (20019), chat closed (20022), room destroyed (20011), connect
// timeout (20005). See the StartCall notes in bridge/PROTOCOL.md.
const CALL_END_OUTPUTS = new Set([20018, 20019, 20022, 20011, 20005]);
const AUDIO_RETRY_MS = 2000;

let logger = null;
let pluginContext = null;
let settings = null;
let controlToken = null;
let controlServer = null;
let avsdkService = null;
let kernelCore = null;
let kernelSession = null;
let listener = null;
let listenerId = null;
let activeSDKInvite = null;
let loginTimer = null;
let acceptTimer = null;
let streamTimer = null;
const streamClients = new Set();
let lastStreamedCall = "";
let capture = null;
let playback = null;
let audioRetryAt = 0;
let dialTimer = null;

const state = {
  startedAt: null,
  listenerRegistered: false,
  listenerError: null,
  serviceAvailable: false,
  serviceNull: null,
  serviceMethods: [],
  eventCount: 0,
  events: [],
  avHost: idleAVHost(),
  call: idleCall(),
  stream: { clients: 0, audio: false, audioError: null },
};

function idleAVHost() {
  return {
    loginPosted: false,
    kernelActionCount: 0,
    outputCount: 0,
    networkOutputCount: 0,
    inviteCallbackSeen: false,
    autoAcceptAttemptedAt: null,
    autoAcceptInviteAt: null,
    autoAcceptPostedAt: null,
    acceptOutputAt: null,
    enterRoomOutputAt: null,
    lastOutputCommand: null,
    lastError: null,
    // Recent AVSDK log lines (output 20050): they echo parsed call parameters.
    logs: [],
  };
}

function idleCall() {
  return {
    phase: "idle",
    inviteAt: null,
    endedAt: null,
    endReason: null,
    inviteType: null,
    callerUid: null,
    callerUin: null,
    callerName: null,
    identityResolvedAt: null,
    identityError: null,
    // True for a call this bridge placed; caller* then names the callee.
    outgoing: false,
  };
}

function integerSetting(value, fallback, name) {
  if (value === undefined || value === null || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(`${name} must be an integer TCP port`);
  }
  return parsed;
}

export function parseBridgeSettings(env = process.env, pluginDir = PLUGIN_DIR) {
  let fileConfig = {};
  const configPath = path.join(pluginDir, "bridge-config.json");
  if (fs.existsSync(configPath)) {
    const parsed = JSON.parse(fs.readFileSync(configPath, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("bridge-config.json must contain an object");
    }
    fileConfig = parsed;
  }
  // The control endpoint may listen beyond loopback (AstrBot in another
  // container); every request but /healthz still needs the token. The AV
  // host endpoint is internal to this machine and stays on loopback.
  const controlHost = env.ASTRBOT_QQ_CALL_BRIDGE_HOST || fileConfig.controlHost || "127.0.0.1";
  const avHost = env.ASTRBOT_QQ_CALL_AV_HOST_HOST || fileConfig.avHost || "127.0.0.1";
  if (!LOOPBACK_HOSTS.has(avHost)) {
    throw new Error("the AV host endpoint must use a loopback host");
  }
  return {
    controlHost,
    controlPort: integerSetting(
      env.ASTRBOT_QQ_CALL_BRIDGE_PORT || fileConfig.controlPort,
      6110,
      "bridge port",
    ),
    avHost,
    avPort: integerSetting(
      env.ASTRBOT_QQ_CALL_AV_HOST_PORT || fileConfig.avPort,
      6111,
      "AV host port",
    ),
    token:
      typeof env.ASTRBOT_QQ_CALL_BRIDGE_TOKEN === "string"
        ? env.ASTRBOT_QQ_CALL_BRIDGE_TOKEN.trim()
        : "",
    tokenFile:
      env.ASTRBOT_QQ_CALL_BRIDGE_TOKEN_FILE || fileConfig.tokenFile || "",
    captureDevice:
      env.ASTRBOT_QQ_CALL_CAPTURE_DEVICE ||
      fileConfig.captureDevice ||
      "astrbot_qq_speaker.monitor",
    playbackDevice:
      env.ASTRBOT_QQ_CALL_PLAYBACK_DEVICE || fileConfig.playbackDevice || "astrbot_qq_mic",
  };
}

function loadControlToken(config) {
  const token = config.token || fs.readFileSync(config.tokenFile, "utf8").trim();
  if (Buffer.byteLength(token, "utf8") < 32) {
    throw new Error("bridge token is missing or shorter than 32 bytes");
  }
  return token;
}

function hasValidControlToken(req, expectedToken) {
  const header = req.headers.authorization ?? "";
  if (!header.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice(7), "utf8");
  const expected = Buffer.from(expectedToken, "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

function sendJson(res, statusCode, body) {
  const encoded = Buffer.from(JSON.stringify(body));
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": encoded.byteLength,
    "Cache-Control": "no-store",
  });
  res.end(encoded);
}

async function readJsonBody(req, limit = 2 * 1024 * 1024) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new Error("request body is too large");
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

export function summarizeValue(value, depth = 0) {
  if (value === null) return null;
  if (value === undefined) return { type: "undefined" };
  if (depth > 2) return { type: typeof value };
  if (Buffer.isBuffer(value) || value instanceof Uint8Array) {
    return { type: "binary", length: value.byteLength };
  }
  if (Array.isArray(value)) {
    return {
      type: "array",
      length: value.length,
      items: value.slice(0, 4).map((item) => summarizeValue(item, depth + 1)),
    };
  }
  if (typeof value === "object") {
    return {
      type: "object",
      keys: Object.keys(value)
        .slice(0, 40)
        .map((key) => ({
          key,
          value: SENSITIVE_KEY.test(key)
            ? "[REDACTED]"
            : summarizeValue(value[key], depth + 1),
        })),
    };
  }
  if (typeof value === "string") return { type: "string", length: value.length };
  if (typeof value === "number" || typeof value === "boolean") {
    return { type: typeof value, value };
  }
  return { type: typeof value };
}

function discoverMethods(value) {
  const methods = new Set();
  let cursor = value;
  for (let depth = 0; cursor && depth < 6; depth += 1) {
    for (const key of Reflect.ownKeys(cursor)) {
      if (typeof key !== "string" || key === "constructor") continue;
      try {
        if (typeof value[key] === "function") methods.add(key);
      } catch {
        // Native getters may throw during discovery.
      }
    }
    cursor = Object.getPrototypeOf(cursor);
  }
  return [...methods].sort();
}

function mapValue(value, key) {
  if (!value) return null;
  if (typeof value.get === "function") return value.get(key) ?? null;
  return typeof value === "object" ? value[key] ?? null : null;
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

async function resolveCallerIdentity(uid, inviteAt) {
  let uin = null;
  let profile = null;
  const errors = [];
  try {
    const result = await kernelSession?.getUixConvertService?.().getUin([uid]);
    const resolved = mapValue(result?.uinInfo, uid);
    if (resolved !== null && String(resolved) !== "0") uin = String(resolved);
  } catch (error) {
    errors.push(`uin: ${error?.message ?? String(error)}`);
  }
  try {
    const profiles = await kernelCore?.eventWrapper?.callNoListenerEvent?.(
      "NodeIKernelProfileService/getCoreAndBaseInfo",
      "nodeStore",
      [uid],
    );
    profile = mapValue(profiles, uid);
    const profileUin = profile?.uin ?? profile?.coreInfo?.uin;
    if (!uin && profileUin !== null && profileUin !== undefined && String(profileUin) !== "0") {
      uin = String(profileUin);
    }
  } catch (error) {
    errors.push(`profile: ${error?.message ?? String(error)}`);
  }
  if (state.call.inviteAt !== inviteAt || state.call.callerUid !== uid) return;
  state.call = {
    ...state.call,
    callerUin: uin,
    callerName: firstString(
      profile?.remark,
      profile?.displayName,
      profile?.nick,
      profile?.nickname,
      profile?.coreInfo?.remark,
      profile?.coreInfo?.nick,
      profile?.simpleInfo?.coreInfo?.nick,
    ),
    identityResolvedAt: new Date().toISOString(),
    identityError: uin ? null : errors.join("; ") || "caller identity lookup returned empty",
  };
}

export function buildAcceptParams(invite) {
  if (!Array.isArray(invite) || invite.length < 12) {
    throw new Error("AVSDK invite callback is incomplete");
  }
  // Verified against QQ Linux AVSDK. invite[2] contains this account, while
  // the native Accept session key must use the inviter at invite[1].
  const params = [
    invite[0],
    invite[1],
    [invite[1]],
    invite[3],
    invite[4],
    Boolean(invite[10]),
    invite[11],
  ];
  const valid =
    typeof params[0] === "number" &&
    typeof params[1] === "string" &&
    Array.isArray(params[2]) &&
    params[2].every((item) => typeof item === "string") &&
    typeof params[3] === "number" &&
    typeof params[4] === "string" &&
    typeof params[5] === "boolean" &&
    typeof params[6] === "string";
  if (!valid) throw new Error("AVSDK invite does not match the native Accept signature");
  return params;
}

// The JSON parameter of AVSDK command 4 (StartCall) for a voice call to a
// friend. Keys and meanings come from libAVSDKPlugin's StartCall parser;
// `overrides` lets a caller try other values without a new bridge release.
export function buildStartCallParams(selfUid, peerUid, overrides = {}) {
  return JSON.stringify({
    scene_id: SCENE_FRIEND,
    self_uid: selfUid,
    relation_id: "0",
    // Maps to app type 0 (audio only) in DavAppTypeForSubBusinessType.
    sub_business_type: 3,
    invite_count: 1,
    invite_uids: [peerUid],
    invite_reason: 0,
    invite_original: 0,
    audio_scene: 0,
    use_ntrtc_dsp: false,
    ntrtc_ai_denoise_update_model: "",
    ...overrides,
  });
}

async function resolveUid(uin) {
  try {
    const uid = await kernelCore?.apis?.UserApi?.getUidByUinV2?.(uin);
    if (typeof uid === "string" && uid) return uid;
  } catch {
    // Fall through to the kernel service.
  }
  const result = await kernelSession?.getUixConvertService?.().getUid([uin]);
  const uid = mapValue(result?.uidInfo, uin);
  return typeof uid === "string" && uid ? uid : null;
}

async function dial(body) {
  const uin = String(body?.uin ?? "").trim();
  if (!/^[0-9]{5,12}$/.test(uin)) throw Object.assign(new Error("uin is invalid"), { status: 400 });
  if (!["idle", "ended", "error"].includes(state.call.phase)) {
    throw Object.assign(new Error("a call is in progress"), { status: 409 });
  }
  const selfUid = String(pluginContext?.core?.selfInfo?.uid ?? "");
  if (!selfUid || !state.avHost.loginPosted) {
    throw Object.assign(new Error("AV host is not logged in"), { status: 503 });
  }
  const peerUid = await resolveUid(uin);
  if (!peerUid) throw Object.assign(new Error("no uid for this uin"), { status: 404 });
  const overrides =
    body?.startCall && typeof body.startCall === "object" && !Array.isArray(body.startCall)
      ? body.startCall
      : {};
  const inviteAt = new Date().toISOString();
  activeSDKInvite = null;
  state.call = {
    ...idleCall(),
    phase: "dialing",
    inviteAt,
    callerUid: peerUid,
    callerUin: uin,
    outgoing: true,
  };
  void resolveCallerIdentity(peerUid, inviteAt);
  await invokeAVHost(4, [buildStartCallParams(selfUid, peerUid, overrides)]);
  if (dialTimer) clearTimeout(dialTimer);
  dialTimer = setTimeout(() => {
    dialTimer = null;
    if (state.call.inviteAt === inviteAt && state.call.phase !== "connected") {
      void hangup("no answer").catch(() => {});
    }
  }, DIAL_TIMEOUT_MS);
  dialTimer.unref?.();
  return { inviteAt, callerUid: peerUid };
}

function endCall(reason) {
  if (dialTimer) clearTimeout(dialTimer);
  dialTimer = null;
  if (acceptTimer) clearTimeout(acceptTimer);
  acceptTimer = null;
  activeSDKInvite = null;
  if (state.call.phase === "idle" || state.call.phase === "ended") return;
  state.call = {
    ...state.call,
    phase: "ended",
    endedAt: new Date().toISOString(),
    endReason: reason,
  };
}

async function hangup(reason = "hangup") {
  const peerUid = state.call.callerUid;
  if (["idle", "ended"].includes(state.call.phase) || !peerUid) return false;
  // Command 10 (Close) ends a one-to-one call, answered or still ringing;
  // Quit (8) is refused for this scene. Reason 0 = QRTCSelfCloseReasonDefault.
  await invokeAVHost(10, [SCENE_FRIEND, peerUid, 0]);
  endCall(reason);
  return true;
}

function invokeAVHost(command, params, retries = 2) {
  return new Promise((resolve, reject) => {
    const encoded = Buffer.from(JSON.stringify({ command, params }));
    const request = http.request(
      {
        host: settings.avHost,
        port: settings.avPort,
        path: "/v1/invoke",
        method: "POST",
        headers: {
          Authorization: `Bearer ${controlToken}`,
          "Content-Type": "application/json",
          "Content-Length": encoded.byteLength,
        },
        timeout: 3000,
      },
      (response) => {
        response.resume();
        response.on("end", () => {
          if (response.statusCode >= 200 && response.statusCode < 300) resolve();
          else reject(new Error(`AV host returned HTTP ${response.statusCode}`));
        });
      },
    );
    request.on("timeout", () => request.destroy(new Error("AV host timeout")));
    request.on("error", (error) => {
      if (retries > 0) {
        setTimeout(() => invokeAVHost(command, params, retries - 1).then(resolve, reject), 250);
      } else reject(error);
    });
    request.end(encoded);
  });
}

async function acceptActiveInvite() {
  if (!Array.isArray(activeSDKInvite) || state.call.phase === "ended") return;
  const inviteAt = state.call.inviteAt;
  if (!inviteAt || state.avHost.autoAcceptInviteAt === inviteAt) return;
  state.avHost.autoAcceptInviteAt = inviteAt;
  state.avHost.autoAcceptAttemptedAt = new Date().toISOString();
  state.call.phase = "accepting";
  await invokeAVHost(5, buildAcceptParams(activeSDKInvite));
  state.avHost.autoAcceptPostedAt = new Date().toISOString();
}

function scheduleAccept(delayMs) {
  if (acceptTimer) clearTimeout(acceptTimer);
  acceptTimer = setTimeout(() => {
    acceptTimer = null;
    void acceptActiveInvite().catch((error) => {
      state.call.phase = "ringing";
      state.avHost.lastError = `native auto-accept failed: ${error?.message ?? String(error)}`;
      logger?.warn(`[AstrBotQQCall] ${state.avHost.lastError}`);
    });
  }, delayMs);
  acceptTimer.unref?.();
}

function forwardKernelAction(name, args) {
  let actionType = null;
  let payload = null;
  if (name.toLowerCase() === "oninviteactiontoavsdk") {
    actionType = args[1];
    payload = args[2];
  } else if (name.toLowerCase() === "onactiontoavsdk") {
    actionType = args[0];
    payload = args[1];
  }
  if (typeof actionType !== "number" || typeof payload !== "string") return;
  void invokeAVHost(55, [actionType, payload]).then(
    () => {
      state.avHost.kernelActionCount += 1;
      state.avHost.lastError = null;
      if (name.toLowerCase() === "onactiontoavsdk" && activeSDKInvite) scheduleAccept(75);
    },
    (error) => {
      state.avHost.lastError = `kernel action forward failed: ${error?.message ?? String(error)}`;
    },
  );
}

function recordEvent(name, args) {
  const now = new Date().toISOString();
  const lowerName = name.toLowerCase();
  if (lowerName === "oninviteactiontoavsdk") {
    activeSDKInvite = null;
    state.avHost.autoAcceptAttemptedAt = null;
    state.avHost.autoAcceptInviteAt = null;
    state.avHost.autoAcceptPostedAt = null;
    state.call = {
      ...idleCall(),
      phase: "ringing",
      inviteAt: now,
      inviteType: typeof args[0]?.invite_type === "number" ? args[0].invite_type : null,
    };
  } else if (lowerName === "ons2cactiontoavsdk" && typeof args[0]?.destroyReason === "number") {
    endCall(args[0].destroyReason);
  }
  state.eventCount += 1;
  state.events.push({ name, at: now, args: args.map((arg) => summarizeValue(arg)) });
  if (state.events.length > MAX_EVENTS) state.events.shift();
  forwardKernelAction(name, args);
}

function makeListener() {
  const target = {};
  for (const name of [
    "onS2CActionToAVSDK",
    "onActionToAVSDK",
    "onAVSDKData",
    "onAVSdkCrash",
    "onReceiveInvite",
    "onInviteActionToAVSDK",
    "onGroupVideoActionToAVSDK",
    "onGroupVideoServerPushToAVSDK",
  ]) {
    target[name] = (...args) => recordEvent(name, args);
  }
  return new Proxy(target, {
    get(object, property, receiver) {
      if (Reflect.has(object, property)) return Reflect.get(object, property, receiver);
      if (typeof property === "string") {
        const callback = (...args) => recordEvent(property, args);
        Reflect.set(object, property, callback);
        return callback;
      }
      return Reflect.get(object, property, receiver);
    },
  });
}

async function handleAVSDKOutput(body) {
  const command = body?.command;
  const value = body?.value;
  if (!Number.isInteger(command)) throw new Error("invalid AVSDK output command");
  state.avHost.outputCount += 1;
  state.avHost.lastOutputCommand = command;
  // 20050 is not a login problem: it carries one AVSDK log line (LogSend),
  // and re-logging in on it looped forever.
  if (command === 120043 && state.avHost.loginPosted && pluginContext) {
    state.avHost.loginPosted = false;
    scheduleAVHostLogin(pluginContext, 100);
  }
  if (command === 20050) {
    const line = Array.isArray(value) ? value.join(" ") : String(value ?? "");
    state.avHost.logs.push(line.slice(0, 500));
    if (state.avHost.logs.length > MAX_AVSDK_LOGS) state.avHost.logs.shift();
    return;
  }
  // 20001 carries signalling to the kernel, 20000 channel registration.
  if (command === 20001 || command === 20000) {
    if (!Array.isArray(value) || typeof value[0] !== "number" || typeof value[1] !== "string") {
      throw new Error("invalid AVSDK network output");
    }
    if (value[1].length > 1024 * 1024) throw new Error("AVSDK network output is too large");
    if (!avsdkService || typeof avsdkService.setActionFromAVSDK !== "function") {
      throw new Error("setActionFromAVSDK is unavailable");
    }
    await avsdkService.setActionFromAVSDK(value[0], value[1]);
    state.avHost.networkOutputCount += 1;
  } else if (command === 20006 && Array.isArray(value)) {
    activeSDKInvite = value;
    const callerUid = typeof value[1] === "string" ? value[1] : null;
    state.call = { ...state.call, callerUid, callerUin: null, callerName: null };
    state.avHost.inviteCallbackSeen = true;
    if (callerUid) void resolveCallerIdentity(callerUid, state.call.inviteAt);
    scheduleAccept(500);
  } else if (command === 5) {
    state.avHost.acceptOutputAt = new Date().toISOString();
    state.call.phase = Array.isArray(value) && value[0] === 0 ? "accepted" : "ringing";
  } else if (command === 20004) {
    state.avHost.enterRoomOutputAt = new Date().toISOString();
    if (Array.isArray(value) && value[0] === 0) {
      state.call.phase = "connected";
      if (dialTimer) clearTimeout(dialTimer);
      dialTimer = null;
    } else if (!state.call.outgoing) {
      state.call.phase = "ringing";
    }
  } else if ((command === 4 || command === 20007) && Array.isArray(value) && value[0] !== 0) {
    // StartCall or the invite itself failed.
    endCall(`${command === 4 ? "start" : "invite"} failed: ${value.slice(0, 3).join(",")}`);
    state.call.phase = "error";
  } else if (command === 20021 && state.call.outgoing && state.call.phase === "dialing") {
    state.call.phase = "ringing"; // the callee's phone rings
  } else if (command === 20020 && state.call.outgoing) {
    state.call.phase = "accepted";
  } else if (CALL_END_OUTPUTS.has(command)) {
    endCall(`avsdk ${command}`);
  }
  state.avHost.lastError = null;
}

export function encodeWsFrame(opcode, payload) {
  const length = payload.byteLength;
  let header;
  if (length < 126) {
    header = Buffer.from([0x80 | opcode, length]);
  } else if (length < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x80 | opcode;
    header[1] = 126;
    header.writeUInt16BE(length, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x80 | opcode;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(length), 2);
  }
  return Buffer.concat([header, payload]);
}

// Splits the complete WebSocket frames off the front of `buffer`.
export function decodeWsFrames(buffer) {
  const frames = [];
  let rest = buffer;
  while (rest.length >= 2) {
    const fin = (rest[0] & 0x80) !== 0;
    const opcode = rest[0] & 0x0f;
    const masked = (rest[1] & 0x80) !== 0;
    let length = rest[1] & 0x7f;
    let offset = 2;
    if (length === 126) {
      if (rest.length < 4) break;
      length = rest.readUInt16BE(2);
      offset = 4;
    } else if (length === 127) {
      if (rest.length < 10) break;
      const big = rest.readBigUInt64BE(2);
      if (big > BigInt(MAX_WS_FRAME)) throw new Error("WebSocket frame is too large");
      length = Number(big);
      offset = 10;
    }
    if (length > MAX_WS_FRAME) throw new Error("WebSocket frame is too large");
    const maskOffset = offset;
    if (masked) offset += 4;
    if (rest.length < offset + length) break;
    const payload = Buffer.from(rest.subarray(offset, offset + length));
    if (masked) {
      for (let i = 0; i < payload.length; i += 1) payload[i] ^= rest[maskOffset + (i % 4)];
    }
    frames.push({ fin, opcode, payload });
    rest = rest.subarray(offset + length);
  }
  return { frames, rest };
}

function broadcast(frame) {
  for (const socket of streamClients) {
    if (!socket.destroyed) socket.write(frame);
  }
}

function stopAudio() {
  for (const child of [capture, playback]) {
    if (child && child.exitCode === null) child.kill("SIGTERM");
  }
  capture = null;
  playback = null;
}

function startAudio() {
  let carry = Buffer.alloc(0);
  const captureChild = spawn(
    "parec",
    [
      ...PCM_ARGS,
      `--device=${settings.captureDevice}`,
      "--latency-msec=20",
      "--client-name=astrbot-qq-call-capture",
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  // Only whole samples go out: a pipe read may end mid-sample.
  captureChild.stdout.on("data", (chunk) => {
    const data = carry.length ? Buffer.concat([carry, chunk]) : chunk;
    const even = data.length & ~1;
    carry = Buffer.from(data.subarray(even));
    if (even) broadcast(encodeWsFrame(2, data.subarray(0, even)));
  });
  const playbackChild = spawn(
    "pacat",
    [
      ...PCM_ARGS,
      `--device=${settings.playbackDevice}`,
      // A short playback buffer keeps barge-in quick.
      "--latency-msec=60",
      "--client-name=astrbot-qq-call-playback",
    ],
    { stdio: ["pipe", "ignore", "pipe"] },
  );
  playbackChild.stdin.on("error", () => {});
  for (const child of [captureChild, playbackChild]) {
    child.on("error", (error) => {
      state.stream.audioError = `${child.spawnfile}: ${error?.message ?? String(error)}`;
    });
    child.on("exit", (code) => {
      if (capture === child || playback === child) {
        state.stream.audioError ??= `${child.spawnfile} exited with ${code}`;
        audioRetryAt = Date.now() + AUDIO_RETRY_MS;
        stopAudio();
      }
    });
    child.stderr.on("data", (chunk) => {
      state.stream.audioError = `${child.spawnfile}: ${String(chunk).trim().slice(0, 300)}`;
    });
  }
  capture = captureChild;
  playback = playbackChild;
  state.stream.audioError = null;
}

// Pushes call state changes and runs call audio while someone listens.
export function streamTick() {
  const call = JSON.stringify(state.call);
  if (call !== lastStreamedCall) {
    lastStreamedCall = call;
    broadcast(encodeWsFrame(1, Buffer.from(JSON.stringify({ type: "call", call: state.call }))));
  }
  const wanted = streamClients.size > 0 && state.call.phase === "connected";
  if (wanted && !capture && Date.now() >= audioRetryAt) startAudio();
  else if (!wanted && capture) stopAudio();
  state.stream.clients = streamClients.size;
  state.stream.audio = Boolean(capture);
}

function attachStream(socket, head) {
  streamClients.add(socket);
  socket.setNoDelay(true);
  socket.write(
    encodeWsFrame(1, Buffer.from(JSON.stringify({ type: "call", call: state.call }))),
  );
  let pending = Buffer.alloc(0);
  const drop = () => {
    streamClients.delete(socket);
    if (!socket.destroyed) socket.destroy();
  };
  const onData = (chunk) => {
    pending = pending.length ? Buffer.concat([pending, chunk]) : chunk;
    let decoded;
    try {
      decoded = decodeWsFrames(pending);
    } catch {
      drop();
      return;
    }
    pending = Buffer.from(decoded.rest);
    for (const frame of decoded.frames) {
      if (!frame.fin || frame.opcode === 0) {
        drop(); // AstrBot never fragments; refuse rather than reassemble.
        return;
      }
      if (frame.opcode === 2) {
        if (playback?.stdin.writable) playback.stdin.write(frame.payload);
      } else if (frame.opcode === 8) {
        streamClients.delete(socket);
        socket.end(encodeWsFrame(8, Buffer.alloc(0)));
        return;
      } else if (frame.opcode === 9) {
        socket.write(encodeWsFrame(10, frame.payload));
      }
    }
  };
  socket.on("data", onData);
  socket.on("close", drop);
  socket.on("error", drop);
  if (head?.length) onData(head);
}

function publicStatus() {
  return {
    version: "0.3.4",
    startedAt: state.startedAt,
    listenerRegistered: state.listenerRegistered,
    listenerError: state.listenerError,
    serviceAvailable: state.serviceAvailable,
    serviceNull: state.serviceNull,
    serviceMethods: state.serviceMethods,
    avHost: state.avHost,
    call: state.call,
    stream: state.stream,
    eventCount: state.eventCount,
    recentEvents: state.events.slice(-10),
  };
}

async function startControlServer() {
  settings = parseBridgeSettings();
  controlToken = loadControlToken(settings);
  controlServer = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${settings.controlHost}:${settings.controlPort}`);
    if (req.method === "GET" && url.pathname === "/healthz") {
      return sendJson(res, 200, { ok: true });
    }
    if (!hasValidControlToken(req, controlToken)) {
      return sendJson(res, 401, { code: -1, message: "Unauthorized" });
    }
    if (req.method === "GET" && url.pathname === "/v1/status") {
      return sendJson(res, 200, { code: 0, data: publicStatus() });
    }
    if (req.method === "GET" && url.pathname === "/v1/calls/current") {
      return sendJson(res, 200, { code: 0, data: state.call });
    }
    if (req.method === "POST" && url.pathname === "/v1/calls/dial") {
      try {
        const data = await dial(await readJsonBody(req, 64 * 1024));
        return sendJson(res, 200, { code: 0, data });
      } catch (error) {
        return sendJson(res, error?.status ?? 500, {
          code: -1,
          message: error?.message ?? String(error),
        });
      }
    }
    if (req.method === "POST" && url.pathname === "/v1/calls/hangup") {
      try {
        return sendJson(res, 200, { code: 0, data: { closed: await hangup() } });
      } catch (error) {
        return sendJson(res, 500, { code: -1, message: error?.message ?? String(error) });
      }
    }
    if (req.method === "POST" && url.pathname === "/v1/avsdk/output") {
      try {
        await handleAVSDKOutput(await readJsonBody(req));
        return sendJson(res, 200, { code: 0, message: "accepted" });
      } catch (error) {
        state.avHost.lastError = error?.message ?? String(error);
        return sendJson(res, 400, { code: -1, message: "invalid AVSDK output" });
      }
    }
    return sendJson(res, 404, { code: -1, message: "Not Found" });
  });
  controlServer.on("clientError", (_error, socket) => socket.destroy());
  controlServer.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url ?? "/", `http://${settings.controlHost}:${settings.controlPort}`);
    const key = req.headers["sec-websocket-key"];
    if (
      url.pathname !== "/v1/stream" ||
      String(req.headers.upgrade ?? "").toLowerCase() !== "websocket" ||
      typeof key !== "string" ||
      !hasValidControlToken(req, controlToken)
    ) {
      socket.end("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      return;
    }
    const accept = createHash("sha1").update(key + WS_GUID).digest("base64");
    socket.write(
      "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
        `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
    );
    attachStream(socket, head);
  });
  streamTimer = setInterval(streamTick, STREAM_TICK_MS);
  streamTimer.unref?.();
  await new Promise((resolve, reject) => {
    controlServer.once("error", reject);
    controlServer.listen(settings.controlPort, settings.controlHost, resolve);
  });
}

function scheduleAVHostLogin(ctx, delayMs = 500) {
  if (loginTimer) clearTimeout(loginTimer);
  loginTimer = setTimeout(async () => {
    try {
      const session = ctx.core?.context?.session;
      const selfUid = String(ctx.core?.selfInfo?.uid ?? "");
      const selfUin = String(ctx.core?.selfInfo?.uin ?? "");
      const accountPath = String(
        session?.getAccountPath?.(Number.parseInt(selfUin, 10)) || ctx.core?.dataPath || "",
      );
      if (!selfUid || !selfUin || !accountPath) throw new Error("QQ identity is unavailable");
      await invokeAVHost(1, [selfUid, selfUin, selfUin, accountPath, ""]);
      state.avHost.loginPosted = true;
      state.avHost.lastError = null;
    } catch (error) {
      state.avHost.lastError = `AV host login failed: ${error?.message ?? String(error)}`;
      scheduleAVHostLogin(ctx, Math.min(delayMs * 2, 5000));
    }
  }, delayMs);
  loginTimer.unref?.();
}

export const plugin_init = async (ctx) => {
  pluginContext = ctx;
  logger = ctx.logger;
  kernelCore = ctx.core ?? null;
  kernelSession = ctx.core?.context?.session ?? null;
  Object.assign(state, {
    startedAt: new Date().toISOString(),
    listenerRegistered: false,
    listenerError: null,
    serviceAvailable: false,
    serviceNull: null,
    serviceMethods: [],
    eventCount: 0,
    events: [],
    avHost: idleAVHost(),
    call: idleCall(),
    stream: { clients: 0, audio: false, audioError: null },
  });
  try {
    avsdkService = kernelSession?.getAVSDKService?.() ?? null;
    state.serviceAvailable = Boolean(avsdkService);
    state.serviceMethods = avsdkService ? discoverMethods(avsdkService) : [];
    state.serviceNull = avsdkService?.isNull ? Boolean(avsdkService.isNull()) : null;
    if (avsdkService?.addKernelAVSDKListener) {
      listener = makeListener();
      listenerId = avsdkService.addKernelAVSDKListener(listener);
      state.listenerRegistered = true;
    }
    await startControlServer();
    scheduleAVHostLogin(ctx);
    logger.info(
      `[AstrBotQQCall] bridge listening on ${settings.controlHost}:${settings.controlPort}`,
    );
    if (!LOOPBACK_HOSTS.has(settings.controlHost)) {
      logger.warn(
        "[AstrBotQQCall] the bridge listens beyond loopback; keep its port off the internet",
      );
    }
  } catch (error) {
    state.listenerError = error?.message ?? String(error);
    logger.error(`[AstrBotQQCall] bridge startup failed: ${state.listenerError}`);
  }
  ctx.router.get("/astrbot-qq-call/status", (_req, res) => {
    res.json({ code: 0, data: publicStatus() });
  });
};

export const plugin_cleanup = async () => {
  pluginContext = null;
  if (loginTimer) clearTimeout(loginTimer);
  if (acceptTimer) clearTimeout(acceptTimer);
  loginTimer = null;
  acceptTimer = null;
  if (streamTimer) clearInterval(streamTimer);
  streamTimer = null;
  if (dialTimer) clearTimeout(dialTimer);
  dialTimer = null;
  for (const socket of streamClients) socket.destroy();
  streamClients.clear();
  lastStreamedCall = "";
  stopAudio();
  if (controlServer) {
    await new Promise((resolve) => controlServer.close(resolve));
    controlServer = null;
  }
  if (avsdkService && listenerId !== null && avsdkService.removeKernelAVSDKListener) {
    try {
      avsdkService.removeKernelAVSDKListener(listenerId);
    } catch (error) {
      logger?.warn(`[AstrBotQQCall] listener cleanup failed: ${error?.message ?? String(error)}`);
    }
  }
  settings = null;
  controlToken = null;
  avsdkService = null;
  kernelCore = null;
  kernelSession = null;
  listener = null;
  listenerId = null;
  activeSDKInvite = null;
  state.listenerRegistered = false;
};

export const plugin_get_config = async () => ({
  controlHost: settings?.controlHost ?? "127.0.0.1",
  controlPort: settings?.controlPort ?? 6110,
  avHost: settings?.avHost ?? "127.0.0.1",
  avPort: settings?.avPort ?? 6111,
});

export const plugin_config_ui = [];
