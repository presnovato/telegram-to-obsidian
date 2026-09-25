// Minimal ambient types for the Electron API surface the plugin uses.
// Full `electron` package is not installed (it would pull a ~100 MB binary);
// the real module comes from Obsidian at runtime and stays external in esbuild.
declare module "electron" {
	export const shell: {
		openPath(path: string): Promise<string>;
	};
}
