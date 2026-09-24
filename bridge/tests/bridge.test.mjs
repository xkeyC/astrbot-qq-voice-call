import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import test from "node:test";

import {
  buildAcceptParams,
  buildStartCallParams,
  decodeWsFrames,
  encodeWsFrame,
  parseBridgeSettings,
  plugin_cleanup,
  plugin_init,
  summarizeValue,
} from "../napcat-plugin/index.mjs";

test("verified native Accept mapping uses the inviter as the session key", () => {
  const invite = [
    1,
    "inviter-uid",
    ["bot-uid"],
    3,
    "relation-id",
    5,
    6,
    7,
    8,
    9,
    1,
    "denoise-model",
  ];
  assert.deepEqual(buildAcceptParams(invite), [
    1,
    "inviter-uid",
    ["inviter-uid"],
    3,
    "relation-id",
    true,
    "denoise-model",
  ]);
});

test("invalid invite shape is rejected before invoking AVSDK", () => {
  assert.throws(() => buildAcceptParams([1, "uid"]), /incomplete/);
  const invite = Array(12).fill(null);
  assert.throws(() => buildAcceptParams(invite), /native Accept signature/);
});

test("diagnostic summaries redact sensitive fields and omit string contents", () => {
  const summary = summarizeValue({
    accessToken: "do-not-log-this",
    callerName: "Alice",
    nested: { cookie: "also-secret" },
  });
  const serialized = JSON.stringify(summary);
  assert.doesNotMatch(serialized, /do-not-log-this|also-secret|Alice/);
  assert.equal(summary.keys.find((item) => item.key === "accessToken").value, "[REDACTED]");
});

test("bridge settings load generated config and allow environment port overrides", () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "astrbot-qq-call-"));
  try {
    fs.writeFileSync(
      path.join(tempDir, "bridge-config.json"),
      JSON.stringify({
        controlHost: "127.0.0.1",
        controlPort: 6110,
        avHost: "localhost",
        avPort: 6111,
        tokenFile: "/run/private/token",
      }),
    );
    const settings = parseBridgeSettings(
      { ASTRBOT_QQ_CALL_BRIDGE_PORT: "6210" },
      tempDir,
    );
    assert.equal(settings.controlPort, 6210);
    assert.equal(settings.avHost, "localhost");
    assert.equal(settings.tokenFile, "/run/private/token");
    assert.equal(settings.token, "");
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});

test("only the internal AV host endpoint must stay on loopback", () => {
  const settings = parseBridgeSettings({ ASTRBOT_QQ_CALL_BRIDGE_HOST: "0.0.0.0" }, os.tmpdir());
  assert.equal(settings.controlHost, "0.0.0.0");
  assert.equal(settings.captureDevice, "astrbot_qq_speaker.monitor");
  assert.equal(settings.playbackDevice, "astrbot_qq_mic");
  assert.throws(
    () => parseBridgeSettings({ ASTRBOT_QQ_CALL_AV_HOST_HOST: "0.0.0.0" }, os.tmpdir()),
    /loopback/,
  );
});

test("WebSocket frames round-trip, masked or not, at every length form", () => {
  for (const size of [0, 5, 300, 70000]) {
    const payload = Buffer.alloc(size, 7);
    const plain = encodeWsFrame(2, payload);
    const masked = Buffer.from(plain);
    // Re-encode as a masked client frame.
    const headerLength = size < 126 ? 2 : size < 65536 ? 4 : 10;
    const mask = Buffer.from([1, 2, 3, 4]);
    const body = Buffer.from(payload.map((byte, i) => byte ^ mask[i % 4]));
    masked[1] |= 0x80;
    const clientFrame = Buffer.concat([masked.subarray(0, headerLength), mask, body]);
    for (const frame of [plain, clientFrame]) {
      const { frames, rest } = decodeWsFrames(Buffer.concat([frame, Buffer.from([0x82])]));
      assert.equal(frames.length, 1);
      assert.equal(frames[0].opcode, 2);
      assert.ok(frames[0].payload.equals(payload));
      assert.equal(rest.length, 1); // an incomplete next frame is kept
    }
  }
  const huge = Buffer.from([0x82, 127, 0, 0, 0, 0, 1, 0, 0, 0]);
  assert.throws(() => decodeWsFrames(huge), /too large/);
});

async function unusedLoopbackPort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const selectedPort = address.port;
  await new Promise((resolve) => server.close(resolve));
  return selectedPort;
}

test("NapCat lifecycle exposes only authenticated call state", async () => {
  const controlPort = await unusedLoopbackPort();
  const avPort = await unusedLoopbackPort();
  const previous = {
    token: process.env.ASTRBOT_QQ_CALL_BRIDGE_TOKEN,
    tokenFile: process.env.ASTRBOT_QQ_CALL_BRIDGE_TOKEN_FILE,
    controlPort: process.env.ASTRBOT_QQ_CALL_BRIDGE_PORT,
    avPort: process.env.ASTRBOT_QQ_CALL_AV_HOST_PORT,
  };
  const token = "test-token-that-is-longer-than-thirty-two-bytes";
  process.env.ASTRBOT_QQ_CALL_BRIDGE_TOKEN = token;
  delete process.env.ASTRBOT_QQ_CALL_BRIDGE_TOKEN_FILE;
  process.env.ASTRBOT_QQ_CALL_BRIDGE_PORT = String(controlPort);
  process.env.ASTRBOT_QQ_CALL_AV_HOST_PORT = String(avPort);

  let registeredListener = null;
  const service = {
    isNull: () => false,
    addKernelAVSDKListener(value) {
      registeredListener = value;
      return 7;
    },
    removeKernelAVSDKListener(id) {
      assert.equal(id, 7);
      registeredListener = null;
    },
  };
  const context = {
    logger: { info() {}, warn() {}, error() {} },
    router: { get() {} },
    core: {
      selfInfo: {},
      context: { session: { getAVSDKService: () => service } },
    },
  };

  try {
    await plugin_init(context);
    assert.ok(registeredListener);
    const baseUrl = `http://127.0.0.1:${controlPort}`;
    const health = await fetch(`${baseUrl}/healthz`);
    assert.equal(health.status, 200);
    const unauthorized = await fetch(`${baseUrl}/v1/calls/current`);
    assert.equal(unauthorized.status, 401);
    const authorized = await fetch(`${baseUrl}/v1/calls/current`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    assert.equal(authorized.status, 200);
    const payload = await authorized.json();
    assert.equal(payload.data.phase, "idle");
    const post = (pathname, body) =>
      fetch(`${baseUrl}${pathname}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    assert.equal((await post("/v1/calls/dial", { uin: "abc" })).status, 400);
    // Not logged in to the AV host yet.
    assert.equal((await post("/v1/calls/dial", { uin: "123456" })).status, 503);
    const hangup = await post("/v1/calls/hangup", {});
    assert.equal((await hangup.json()).data.closed, false);
    // A log line from AVSDK is kept for diagnostics, not taken as a login problem.
    assert.equal(
      (await post("/v1/avsdk/output", { command: 20050, value: ["StartCall scene=1"] })).status,
      200,
    );
    const status = await fetch(`${baseUrl}/v1/status`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    assert.deepEqual((await status.json()).data.avHost.logs, ["StartCall scene=1"]);

    const upgrade = (auth) =>
      new Promise((resolve, reject) => {
        const socket = net.connect(controlPort, "127.0.0.1");
        let data = Buffer.alloc(0);
        socket.on("data", (chunk) => {
          data = Buffer.concat([data, chunk]);
          const end = data.indexOf("\r\n\r\n");
          if (end < 0) return;
          const status = data.subarray(0, end).toString();
          const { frames } = decodeWsFrames(data.subarray(end + 4));
          if (!status.includes(" 101 ") || frames.length) {
            socket.destroy();
            resolve({ status, frames });
          }
        });
        socket.on("error", reject);
        socket.on("end", () => resolve({ status: data.toString(), frames: [] }));
        socket.write(
          "GET /v1/stream HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n" +
            "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n" +
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
            (auth ? `Authorization: Bearer ${token}\r\n` : "") +
            "\r\n",
        );
      });
    const refused = await upgrade(false);
    assert.match(refused.status, / 401 /);
    const accepted = await upgrade(true);
    assert.match(accepted.status, / 101 /);
    assert.match(accepted.status, /s3pPLMBiTxaQ9kYGzzhZRbK\+xOo=/);
    const hello = JSON.parse(accepted.frames[0].payload.toString());
    assert.equal(hello.type, "call");
    assert.equal(hello.call.phase, "idle");
  } finally {
    await plugin_cleanup();
    for (const [key, value] of Object.entries(previous)) {
      const envName = {
        token: "ASTRBOT_QQ_CALL_BRIDGE_TOKEN",
        tokenFile: "ASTRBOT_QQ_CALL_BRIDGE_TOKEN_FILE",
        controlPort: "ASTRBOT_QQ_CALL_BRIDGE_PORT",
        avPort: "ASTRBOT_QQ_CALL_AV_HOST_PORT",
      }[key];
      if (value === undefined) delete process.env[envName];
      else process.env[envName] = value;
    }
  }
  assert.equal(registeredListener, null);
});

test("StartCall parameters describe a voice call to one friend", () => {
  const params = JSON.parse(buildStartCallParams("u_self", "u_peer", { relation_id: "9" }));
  assert.equal(params.scene_id, 1);
  assert.equal(params.self_uid, "u_self");
  assert.deepEqual(params.invite_uids, ["u_peer"]);
  assert.equal(params.invite_count, 1);
  assert.equal(params.sub_business_type, 3);
  assert.equal(params.relation_id, "9");
});
