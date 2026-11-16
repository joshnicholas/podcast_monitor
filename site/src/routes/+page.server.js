import fs from 'node:fs';
import path from 'node:path';

export const prerender = true;

export function load() {
	const manifestPath = path.join(process.cwd(), 'static', 'feeds', 'manifest.json');
	let podcasts = [];

	if (fs.existsSync(manifestPath)) {
		podcasts = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
	}

	return { podcasts };
}
