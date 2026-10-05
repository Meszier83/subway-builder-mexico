const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
module.exports = fs.readFileSync(path.join(root, 'tools/templates/wizard.html'), 'utf8') + '\n' +
  fs.readFileSync(path.join(root, 'tools/static/wizard/app.js'), 'utf8');
