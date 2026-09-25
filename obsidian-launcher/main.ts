import { Menu, Notice, Plugin } from "obsidian";
import { execFile, spawn, type ChildProcess } from "child_process";
import { readFileSync } from "fs";
import { join } from "path";
import { shell } from "electron";
import {
	DEFAULT_SETTINGS,
	effectiveLogPath,
	validateSettings,
	T2oSettingTab,
	type T2oSettings,
} from "./settings";
import {
	classifyExit,
	decideExternalAction,
	nextRestartDelay,
	pruneCrashes,
} from "./supervision";

const RING_MAX = 200;
const TAIL_FOR_NOTICE = 8;
const EXTERNAL_POLL_MS = 60_000;

type BotStatus = "stopped" | "starting" | "running" | "external" | "error";

const STATUS_TEXT: Record<BotStatus, string> = {
	stopped: "остановлен",
	starting: "запуск…",
	running: "работает",
	external: "запущен вне Obsidian",
	error: "ошибка",
};

/** Splits "-m tiktok_obsidian" (honours double quotes) for spawn(). */
export function splitArgs(raw: string): string[] {
	const parts = raw.match(/"[^"]*"|\S+/g) ?? [];
	return parts.map((p) => (p.startsWith('"') ? p.slice(1, -1) : p));
}

export default class T2oLauncherPlugin extends Plugin {
	settings: T2oSettings = DEFAULT_SETTINGS;
	status: BotStatus = "stopped";
	statusBar: HTMLElement | null = null;
	child: ChildProcess | null = null;
	stoppedByUs = false;
	pendingStart = false;
	crashTimes: number[] = [];
	restartTimer: ReturnType<typeof setTimeout> | null = null;
	externalTimer: ReturnType<typeof setInterval> | null = null;
	ring: string[] = [];

	async onload(): Promise<void> {
		await this.loadSettings();
		this.statusBar = this.addStatusBarItem();
		this.updateStatus("stopped");
		this.statusBar.onclick = (ev) => this.showMenu(ev);
		this.addSettingTab(new T2oSettingTab(this.app, this));
		this.addCommand({
			id: "start-bot",
			name: "Start bot",
			callback: () => this.startBot(),
		});
		this.addCommand({
			id: "stop-bot",
			name: "Stop bot",
			callback: () => this.stopBot(),
		});
		this.addCommand({
			id: "restart-bot",
			name: "Restart bot",
			callback: () => this.restartBot(),
		});
		this.addCommand({
			id: "open-bot-log",
			name: "Open bot log",
			callback: () => void this.openLog(),
		});
		this.app.workspace.onLayoutReady(() => {
			if (this.settings.startWithObsidian) this.startBot();
		});
	}

	onunload(): void {
		// Fast path only; onunload is unreliable on quit — the parent
		// watchdog (§3.2) is the guarantee. Never touch a foreign bot.
		// taskkill /F даёт код 1: помечаем свою остановку и снимаем слушатель,
		// чтобы мёртвый плагин не слал нотисы.
		this.clearRestartTimer();
		this.clearExternalTimer();
		this.stoppedByUs = true;
		if (this.child) {
			this.child.removeAllListeners("exit");
			this.taskkill(this.child);
		}
		this.child = null;
	}

	async loadSettings(): Promise<void> {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
	}

	updateStatus(s: BotStatus): void {
		this.status = s;
		this.statusBar?.setText(`t2o: ${STATUS_TEXT[s]}`);
	}

	pushOutput(chunk: string): void {
		for (const line of chunk.split(/\r?\n/)) {
			if (!line.trim()) continue;
			this.ring.push(line);
			if (this.ring.length > RING_MAX) this.ring.shift();
		}
	}

	startBot(): void {
		if (this.child) return; // ours is already running
		this.clearExternalTimer();
		const problem = validateSettings(this.settings);
		if (problem) {
			new Notice(problem);
			this.updateStatus("error");
			return;
		}
		const args = [
			...splitArgs(this.settings.botArgs),
			"--parent-pid",
			String(process.pid),
		];
		this.updateStatus("starting");
		this.stoppedByUs = false;
		this.pendingStart = false;
		try {
			this.child = spawn(this.settings.pythonPath, args, {
				cwd: this.settings.workDir,
				windowsHide: true,
				stdio: ["ignore", "pipe", "pipe"],
			});
		} catch (e) {
			this.child = null;
			this.updateStatus("error");
			new Notice(`Не удалось запустить бота: ${String(e)}`);
			return;
		}
		this.child.stdout?.on("data", (d) => this.pushOutput(String(d)));
		this.child.stderr?.on("data", (d) => this.pushOutput(String(d)));
		this.child.on("error", () => {
			this.child = null;
			this.updateStatus("error");
			new Notice("Не удалось запустить бота: ошибка spawn");
		});
		this.child.on("exit", (code) => this.onChildExit(code));
		this.updateStatus("running");
	}

	onChildExit(code: number | null): void {
		this.child = null;
		const reaction = classifyExit(code, this.stoppedByUs);
		this.stoppedByUs = false;
		if (reaction === "clean") {
			this.updateStatus("stopped");
			if (this.pendingStart) {
				this.pendingStart = false;
				this.startBot();
			}
			return;
		}
		if (reaction === "already-running") {
			this.updateStatus("external");
			new Notice("Бот уже запущен в другом месте");
			this.watchExternal();
			return;
		}
		if (reaction === "config-error") {
			this.updateStatus("error");
			const tail = this.ring.slice(-TAIL_FOR_NOTICE).join("\n");
			new Notice(`Бот не стартовал (ошибка настроек):\n${tail}`);
			return;
		}
		const now = Date.now();
		this.crashTimes = [...pruneCrashes(this.crashTimes, now), now];
		const delay = nextRestartDelay(this.crashTimes.length);
		if (delay === null) {
			this.updateStatus("error");
			new Notice("Бот падает повторно — авторестарт остановлен");
			return;
		}
		this.updateStatus("starting");
		new Notice(`Бот упал (код ${code}), рестарт через ${delay / 1000} с`);
		this.restartTimer = setTimeout(() => {
			this.restartTimer = null;
			this.startBot();
		}, delay);
	}

	stopBot(): void {
		this.clearRestartTimer();
		this.clearExternalTimer();
		this.pendingStart = false;
		if (!this.child) {
			this.updateStatus("stopped");
			return;
		}
		this.stoppedByUs = true;
		this.taskkill(this.child);
	}

	restartBot(): void {
		this.clearRestartTimer();
		this.clearExternalTimer();
		if (!this.child) {
			this.startBot();
			return;
		}
		this.stoppedByUs = true;
		this.pendingStart = true;
		this.taskkill(this.child);
	}

	async openLog(): Promise<void> {
		const err = await shell.openPath(effectiveLogPath(this.settings));
		if (err) new Notice(`Не удалось открыть лог: ${err}`);
	}

	showMenu(ev: MouseEvent): void {
		const menu = new Menu();
		menu.addItem((i) =>
			i.setTitle("Start bot").onClick(() => this.startBot()),
		);
		menu.addItem((i) => i.setTitle("Stop bot").onClick(() => this.stopBot()));
		menu.addItem((i) =>
			i.setTitle("Restart bot").onClick(() => this.restartBot()),
		);
		menu.addItem((i) =>
			i.setTitle("Open bot log").onClick(() => void this.openLog()),
		);
		menu.showAtMouseEvent(ev);
	}

	private clearRestartTimer(): void {
		if (this.restartTimer !== null) {
			clearTimeout(this.restartTimer);
			this.restartTimer = null;
		}
	}

	/** While «запущен вне Obsidian», poll the foreign PID from bot.lock. */
	private watchExternal(): void {
		this.clearExternalTimer();
		this.externalTimer = setInterval(() => this.pollExternal(), EXTERNAL_POLL_MS);
	}

	private clearExternalTimer(): void {
		if (this.externalTimer !== null) {
			clearInterval(this.externalTimer);
			this.externalTimer = null;
		}
	}

	private readLockPid(): number | null {
		try {
			const raw = readFileSync(join(this.settings.workDir, "bot.lock"), "utf-8");
			const pid = Number.parseInt(raw.trim(), 10);
			return Number.isSafeInteger(pid) && pid > 0 ? pid : null;
		} catch {
			return null;
		}
	}

	private static isPidAlive(pid: number): boolean {
		// Node's kill(pid, 0) is a libuv existence test — safe on Windows,
		// unlike Python's os.kill which terminates the target.
		try {
			process.kill(pid, 0);
			return true;
		} catch {
			return false;
		}
	}

	pollExternal(): void {
		if (this.status !== "external") {
			this.clearExternalTimer();
			return;
		}
		const pid = this.readLockPid();
		const action = decideExternalAction(
			pid,
			pid !== null && T2oLauncherPlugin.isPidAlive(pid),
			this.settings.startWithObsidian,
		);
		if (action === "wait") return;
		this.clearExternalTimer();
		if (action === "start") {
			this.updateStatus("stopped");
			new Notice("Внешний бот остановлен, запускаю свой");
			this.startBot();
		} else {
			this.updateStatus("stopped");
			new Notice("Внешний бот остановлен");
		}
	}

	/** Kills the whole process tree. Only ever called for OUR child. */
	private taskkill(child: ChildProcess): void {
		if (child.pid === undefined) return;
		execFile("taskkill", ["/PID", String(child.pid), "/T", "/F"], () => {
			/* exit event carries the outcome */
		});
	}
}
