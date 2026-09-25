// Pure supervision policy: exit-code classification + crash back-off.
// Tested with `npm test` (node --test). No imports — runs anywhere.
// NOTE: erasable TypeScript only (no enums/namespaces), so plain node can run it.

export type ExitReaction = "clean" | "config-error" | "already-running" | "crash";

/** Maps a child exit to a reaction. `stoppedByUs` wins: our own Stop/Restart. */
export function classifyExit(code: number | null, stoppedByUs: boolean): ExitReaction {
	if (stoppedByUs) return "clean";
	if (code === 0) return "clean";
	// Code 4 is the dedicated config error. Code 1 is what taskkill /F and
	// import-time crashes produce — for us that is a crash, not a config error.
	if (code === 4) return "config-error";
	if (code === 3) return "already-running";
	// Any other code, or null when killed by a signal, is a crash.
	return "crash";
}

export const RESTART_DELAYS_MS: readonly number[] = [5_000, 30_000, 120_000];
export const CRASH_WINDOW_MS = 10 * 60 * 1000;
export const MAX_CRASHES_IN_WINDOW = 3;

/** Drops crash timestamps older than the 10-minute window. */
export function pruneCrashes(timestamps: readonly number[], now: number): number[] {
	return timestamps.filter((t) => now - t < CRASH_WINDOW_MS);
}

/**
 * Delay before the next restart, by crash count in the window (latest included):
 * 1st → 5 s, 2nd → 30 s, 3rd → 120 s; more than 3 → null (give up, status error).
 */
export function nextRestartDelay(crashesInWindow: number): number | null {
	if (crashesInWindow < 1 || crashesInWindow > MAX_CRASHES_IN_WINDOW) return null;
	return RESTART_DELAYS_MS[crashesInWindow - 1];
}

export type ExternalAction = "wait" | "stopped" | "start";

/**
 * What to do on one external-status poll tick. `lockPid` is the PID from
 * bot.lock (null when unreadable), `pidAlive` — Node's process.kill(pid, 0)
 * existence test (libuv, safe on Windows — unlike Python's os.kill).
 */
export function decideExternalAction(
	lockPid: number | null,
	pidAlive: boolean,
	startWithObsidian: boolean,
): ExternalAction {
	if (lockPid !== null && pidAlive) return "wait";
	return startWithObsidian ? "start" : "stopped";
}
