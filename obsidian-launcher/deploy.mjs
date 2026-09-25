// Copies main.js + manifest.json into the vault's plugin folder.
// Vault path: T2O_VAULT_DIR env var, or ./deploy.local.json {"vaultDir": "..."}.
// deploy.local.json is personal and gitignored — never hardcode the vault path.
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "fs";
import { join } from "path";

function vaultDir() {
	if (process.env.T2O_VAULT_DIR) return process.env.T2O_VAULT_DIR;
	const local = new URL("./deploy.local.json", import.meta.url);
	if (existsSync(local)) {
		const parsed = JSON.parse(readFileSync(local, "utf-8"));
		if (parsed.vaultDir) return parsed.vaultDir;
	}
	throw new Error("Set T2O_VAULT_DIR or create deploy.local.json with {\"vaultDir\": \"...\"}");
}

const target = join(vaultDir(), ".obsidian", "plugins", "t2o-launcher");
mkdirSync(target, { recursive: true });
for (const file of ["main.js", "manifest.json"]) {
	copyFileSync(new URL(`./${file}`, import.meta.url), join(target, file));
	console.log(`deployed ${file} -> ${target}`);
}
