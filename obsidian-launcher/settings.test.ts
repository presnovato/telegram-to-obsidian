import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_SETTINGS, validateSettings, type T2oSettings } from "./settings-core.ts";

function settings(overrides: Partial<T2oSettings> = {}): T2oSettings {
	return { ...DEFAULT_SETTINGS, ...overrides };
}

describe("DEFAULT_SETTINGS", () => {
	it("has no personal machine paths baked in", () => {
		assert.equal(DEFAULT_SETTINGS.pythonPath, "");
		assert.equal(DEFAULT_SETTINGS.workDir, "");
	});
});

describe("validateSettings", () => {
	it("asks for python and bot folder when both are empty", () => {
		assert.match(
			validateSettings(settings()) ?? "",
			/Укажи путь к python\.exe из venv бота и папку бота в настройках плагина/,
		);
	});

	it("asks for python and bot folder when only one is empty", () => {
		assert.ok(validateSettings(settings({ pythonPath: "python.exe" })));
		assert.ok(validateSettings(settings({ workDir: "C:\\bot" })));
	});

	it("reports a missing python executable", () => {
		const problem = validateSettings(
			settings({ pythonPath: "C:\\nope\\python.exe", workDir: process.cwd() }),
		);
		assert.match(problem ?? "", /Python не найден/);
	});

	it("reports a missing working directory", () => {
		const problem = validateSettings(
			settings({ pythonPath: process.execPath, workDir: "C:\\nope\\bot" }),
		);
		assert.match(problem ?? "", /Рабочая папка не найдена/);
	});

	it("accepts existing paths", () => {
		assert.equal(
			validateSettings(
				settings({ pythonPath: process.execPath, workDir: process.cwd() }),
			),
			null,
		);
	});
});
