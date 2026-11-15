const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('custom_components/nc_cookbook/panel/index.html', 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const calls = [];
const hass = {
  language:'en',
  fetchWithAuth: async (...args) => { calls.push(args); return {ok:true}; },
  callWS: async msg => { calls.push(msg); return {path:'/signed-image'}; }
};
const element = () => ({addEventListener(){}, style:{}, classList:{add(){}, remove(){}}, value:'', querySelectorAll(){return [];}});
const context = {console, URL, navigator:{language:'en'}, setTimeout, clearTimeout, setInterval, clearInterval,
  window:{parent:{document:{querySelector:()=>({hass})}},addEventListener(){}},
  document:{getElementById:element, querySelectorAll:()=>[], documentElement:{style:{setProperty(){}}}}
};
vm.createContext(context);
vm.runInContext(source, context);
(async()=>{
  await context.proxyFetch('recipes');
  assert.equal(calls[0][0], '/api/nc_cookbook/recipes');
  await context.proxyWrite('recipes', 'POST', {name:'Soup'});
  assert.equal(calls[1][1].method, 'POST');
  assert.equal(JSON.parse(calls[1][1].body).name, 'Soup');
  const img = {isConnected:true,style:{}};
  await context.setRecipeImage(img, 'test-id');
  assert.equal(calls[2].type, 'auth/sign_path');
  assert.equal(img.src, '/signed-image');
  assert.ok(!source.includes('fetch(api('));
  assert.ok(!source.includes('config.json'));
  console.log('Frontend: authenticated reads/writes, signed images and syntax OK');
})().catch(e=>{console.error(e); process.exitCode=1;});
