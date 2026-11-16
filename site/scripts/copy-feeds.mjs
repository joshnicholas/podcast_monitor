import fs from 'node:fs';
import path from 'node:path';

const dataDir = path.join(import.meta.dirname, '..', '..', 'data');
const outDir = path.join(import.meta.dirname, '..', 'static', 'feeds');

fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });

const manifest = [];

if (fs.existsSync(dataDir)) {
	for (const name of fs.readdirSync(dataDir)) {
		const src = path.join(dataDir, name, 'feed.xml');
		if (!fs.existsSync(src)) continue;

		const xml = fs.readFileSync(src, 'utf-8');
		fs.copyFileSync(src, path.join(outDir, `${name}.xml`));

		const match = xml.match(/<title>([^<]*)<\/title>/);
		manifest.push({ name, display_name: match ? match[1] : name });
	}
}

fs.writeFileSync(path.join(outDir, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log(`Copied ${manifest.length} feed(s).`);
