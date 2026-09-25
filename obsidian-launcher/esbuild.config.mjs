import esbuild from "esbuild";
import process from "process";

const prod = process.argv[2] === "production";

esbuild
	.build({
		entryPoints: ["main.ts"],
		bundle: true,
		external: ["obsidian", "electron", "child_process", "fs", "path", "os"],
		format: "cjs",
		target: "es2020",
		logLevel: "info",
		sourcemap: prod ? false : "inline",
		treeShaking: true,
		outfile: "main.js",
	})
	.catch(() => process.exit(1));
