/* npm ci --prefix tools/wizard-assets; node tools/build_wizard_assets.cjs */
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const packages = path.join(__dirname, 'wizard-assets/node_modules');
const output = path.join(__dirname, 'static/wizard');
fs.mkdirSync(path.join(output, 'vendor/images'), {recursive:true});
for (const name of ['leaflet.js', 'leaflet.css'])
  fs.copyFileSync(path.join(packages, 'leaflet/dist', name), path.join(output, 'vendor', name));
for (const name of fs.readdirSync(path.join(packages, 'leaflet/dist/images')))
  fs.copyFileSync(path.join(packages, 'leaflet/dist/images', name), path.join(output, 'vendor/images', name));
fs.copyFileSync(path.join(packages, 'leaflet/LICENSE'), path.join(output, 'vendor/leaflet-LICENSE'));
fs.copyFileSync(path.join(packages, 'lucide/dist/umd/lucide.min.js'), path.join(output, 'vendor/lucide.min.js'));
fs.copyFileSync(path.join(packages, 'lucide/LICENSE'), path.join(output, 'vendor/lucide-LICENSE'));
fs.copyFileSync(path.join(packages, 'tailwindcss/LICENSE'), path.join(output, 'tailwind-LICENSE'));
execFileSync(process.execPath, [path.join(packages, 'tailwindcss/lib/cli.js'),
  '-c', 'tools/wizard-assets/tailwind.config.cjs', '-i', 'tools/wizard-assets/tailwind.css',
  '-o', 'tools/static/wizard/tailwind.css', '--minify'], {cwd:root, stdio:'inherit'});
const files = ['app.js', 'app.css', 'bootstrap.js', 'tailwind.css', 'vendor/leaflet.js', 'vendor/leaflet.css', 'vendor/lucide.min.js'];
const crypto = require('node:crypto');
fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify({
  dependencies: {leaflet:'1.9.4', lucide:'0.468.0', tailwindcss:'3.4.19'},
  files: Object.fromEntries(files.map(name => [name, crypto.createHash('sha256').update(fs.readFileSync(path.join(output, name))).digest('hex')]))
}, null, 2) + '\n');
