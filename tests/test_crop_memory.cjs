const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/../web/app.js`, 'utf8');
const memory = new Map();
const context = vm.createContext({
  localStorage: { getItem: key => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) },
  editorRequest: async () => ({region: [0.6, 0.9, 0.04, 0.96]}),
});
const start = source.indexOf('  function isValidCrop(');
const end = source.indexOf('  function showSavedBadge(', start);
vm.runInContext(`const STORAGE_KEYS = {LAST_CROP:'last',VIDEO_CROP_PREFIX:'video:'};
  const state = {regions:[],activeRegion:0,currentVideo:'A',crop:{ymin:0.1,ymax:0.3,xmin:0,xmax:1}};
  ${source.slice(start, end)}
  saveCropToLocal(true);
  state.currentVideo = 'B';
  state.crop = {ymin:0.5,ymax:0.7,xmin:0,xmax:1};
  saveCropToLocal(true);
  localStorage.setItem('video:C', JSON.stringify({ymin:0.2,ymax:0.4,xmin:0,xmax:1}));
`, context);
assert.equal(vm.runInContext("savedRegions('A')[0].ymin", context), 0.1);
assert.equal(vm.runInContext("savedRegions('B')[0].ymin", context), 0.5);
assert.equal(vm.runInContext("savedRegions('C')[0].ymin", context), 0.2);
(async () => {
  assert.equal((await vm.runInContext("detectRegion('D', 'folder')", context))[0].ymin, 0.6);
  assert.equal((await vm.runInContext("detectRegion('A', 'folder')", context))[0].ymin, 0.1);
  console.log('Per-video crop restore, legacy crop and detection persistence passed');
})();
