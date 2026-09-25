// Settings model and validation, free of any `obsidian` import so it can be
// unit-tested under plain Node. The Obsidian settings UI lives in settings.ts.
import { existsSync } from "fs";

export interface T2oSettings {
	pythonPath: string;
	workDir: string;
	/** Extra args, e.g. "-m tiktok_obsidian". The plugin appends --parent-pid itself. */
	botArgs: string;
	startWithObsidian: boolean;
	/** "" = <workDir>/logs/bot.log */
	logPath: string;
}

export const DEFAULT_SETTINGS: T2oSettings = {
	pythonPath: "",
	workDir: "",
	botArgs: "-m tiktok_obsidian",
	startWithObsidian: true,
	logPath: "",
};

/** Real log path: explicit setting, or the bot's default under the working dir. */
export function effectiveLogPath(s: T2oSettings): string {
	if (s.logPath.trim()) return s.logPath.trim();
	return s.workDir.replace(/[\\/]+$/, "") + "\\logs\\bot.log";
}

/** Null when settings are usable; otherwise a human-readable reason. */
export function validateSettings(s: T2oSettings): string | null {
	if (!s.pythonPath.trim() || !s.workDir.trim())
		return "Укажи путь к python.exe из venv бота и папку бота в настройках плагина";
	if (!existsSync(s.pythonPath)) return `Python не найден: ${s.pythonPath}`;
	if (!existsSync(s.workDir)) return `Рабочая папка не найдена: ${s.workDir}`;
	return null;
}
