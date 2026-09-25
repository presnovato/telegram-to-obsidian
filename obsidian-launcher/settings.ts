import { App, PluginSettingTab, Setting } from "obsidian";
import {
	DEFAULT_SETTINGS,
	effectiveLogPath,
	validateSettings,
	type T2oSettings,
} from "./settings-core";

export { DEFAULT_SETTINGS, effectiveLogPath, validateSettings };
export type { T2oSettings };

export class T2oSettingTab extends PluginSettingTab {
	plugin: { settings: T2oSettings; saveSettings(): Promise<void> };

	constructor(app: App, plugin: { settings: T2oSettings; saveSettings(): Promise<void> }) {
		super(app, plugin as never);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();
		containerEl.createEl("h2", { text: "TikTok → Obsidian bot" });

		const errorEl = containerEl.createDiv();
		const refreshError = () => {
			const problem = validateSettings(this.plugin.settings);
			errorEl.setText(problem ?? "");
			errorEl.style.color = "var(--text-error)";
		};

		const text = (
			name: string,
			desc: string,
			get: () => string,
			set: (v: string) => void,
		) => {
			new Setting(containerEl)
				.setName(name)
				.setDesc(desc)
				.addText((t) =>
					t.setValue(get()).onChange(async (v) => {
						set(v);
						await this.plugin.saveSettings();
						refreshError();
					}),
				);
		};

		text(
			"Python executable",
			"python.exe из venv бота (не pythonw: под ним у бота нет stdout и ломается лог).",
			() => this.plugin.settings.pythonPath,
			(v) => (this.plugin.settings.pythonPath = v),
		);
		text(
			"Bot working directory",
			"Корень репозитория бота.",
			() => this.plugin.settings.workDir,
			(v) => (this.plugin.settings.workDir = v),
		);
		text(
			"Arguments",
			"Плагин сам дописывает --parent-pid.",
			() => this.plugin.settings.botArgs,
			(v) => (this.plugin.settings.botArgs = v),
		);
		text(
			"Log file",
			"Пусто = <working dir>/logs/bot.log.",
			() => this.plugin.settings.logPath,
			(v) => (this.plugin.settings.logPath = v),
		);
		new Setting(containerEl)
			.setName("Start with Obsidian")
			.setDesc("Запускать бота при открытии хранилища.")
			.addToggle((t) =>
				t
					.setValue(this.plugin.settings.startWithObsidian)
					.onChange(async (v) => {
						this.plugin.settings.startWithObsidian = v;
						await this.plugin.saveSettings();
					}),
			);
		refreshError();
	}
}
