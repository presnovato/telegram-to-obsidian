import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
	classifyExit,
	nextRestartDelay,
	pruneCrashes,
	decideExternalAction,
	CRASH_WINDOW_MS,
} from "./supervision.ts";

describe("classifyExit", () => {
	it("clean stop", () => {
		assert.equal(classifyExit(0, false), "clean");
	});
	it("our own stop/restart is always clean", () => {
		assert.equal(classifyExit(1, true), "clean");
		assert.equal(classifyExit(137, true), "clean");
		assert.equal(classifyExit(null, true), "clean");
	});
	it("config error is not restarted", () => {
		assert.equal(classifyExit(4, false), "config-error");
	});
	it("external instance is not restarted", () => {
		assert.equal(classifyExit(3, false), "already-running");
	});
	it("kill code 1 and other codes are crashes", () => {
		assert.equal(classifyExit(1, false), "crash");
		assert.equal(classifyExit(2, false), "crash");
		assert.equal(classifyExit(99, false), "crash");
		assert.equal(classifyExit(null, false), "crash");
	});
});

describe("restart back-off", () => {
	it("delays 5s, 30s, 120s then gives up", () => {
		assert.equal(nextRestartDelay(1), 5_000);
		assert.equal(nextRestartDelay(2), 30_000);
		assert.equal(nextRestartDelay(3), 120_000);
		assert.equal(nextRestartDelay(4), null);
		assert.equal(nextRestartDelay(0), null);
	});
	it("prunes crashes outside the 10-minute window", () => {
		const now = 1_000_000;
		const kept = [now - 1000, now - CRASH_WINDOW_MS + 1000];
		const pruned = pruneCrashes([now - CRASH_WINDOW_MS - 1, ...kept], now);
		assert.deepEqual(pruned, kept);
	});
});

describe("decideExternalAction", () => {
	it("waits while the foreign PID is alive", () => {
		assert.equal(decideExternalAction(1234, true, true), "wait");
		assert.equal(decideExternalAction(1234, true, false), "wait");
	});
	it("dead PID restarts own bot when auto-start is on", () => {
		assert.equal(decideExternalAction(1234, false, true), "start");
	});
	it("dead PID just stops when auto-start is off", () => {
		assert.equal(decideExternalAction(1234, false, false), "stopped");
	});
	it("unreadable lock is treated as gone", () => {
		assert.equal(decideExternalAction(null, false, true), "start");
		assert.equal(decideExternalAction(null, false, false), "stopped");
	});
});
