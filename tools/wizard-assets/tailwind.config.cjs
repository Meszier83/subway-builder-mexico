module.exports = {
  content: ['./tools/templates/wizard.html', './tools/static/wizard/app.js', './tools/static/wizard/bootstrap.js'],
  theme: {extend: {
    colors: {metro: {orange:'#D95F18', green:'#15803D', pink:'#C80068', blue:'#0284C7',
      dark:'#F7F5F0', card:'#FFFFFF', panel:'#F3EFE6', border:'#D8D1C5', text:'#181615', muted:'#524B42', accent:'#D95F18'}},
    fontFamily: {sans:['Inter','system-ui','-apple-system','Segoe UI','Roboto','sans-serif'],
      mono:['JetBrains Mono','Menlo','Consolas','monospace']},
  }},
};
