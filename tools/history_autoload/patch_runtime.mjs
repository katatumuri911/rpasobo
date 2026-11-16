import fs from 'fs';

let content = fs.readFileSync('runtime-DL3Dibca.orig.js', 'utf8');

// 1. nd
const target1 = 'nd={historyCursor:null,hasMoreHistory:!1,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:null};';
const repl1 = 'nd={historyCursor:null,hasMoreHistory:!1,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:null,addedItems:null,emptyPageStreak:0,pageCount:0,lastPageReason:null};';
if (!content.includes(target1)) throw new Error('target1 not found');
content = content.replace(target1, repl1);

// 2. ef
const target2 = 'function ef(e){return{historyCursor:e.historyCursor??null,hasMoreHistory:e.hasMoreHistory??!1,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:e.latestLocalTurnOrdinal??null}}';
const repl2 = 'function ef(e){return{historyCursor:e.historyCursor??null,hasMoreHistory:e.hasMoreHistory??!1,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:e.latestLocalTurnOrdinal??null,addedItems:null,emptyPageStreak:0,pageCount:0,lastPageReason:null}}';
if (!content.includes(target2)) throw new Error('target2 not found');
content = content.replace(target2, repl2);

// 3. snapshot history init branch 1
const target3 = 't?{history:{historyCursor:e.historyCursor??null,hasMoreHistory:e.hasMoreHistory??!1,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:e.latestLocalTurnOrdinal??null}}:{resetHistory:!0}';
const repl3 = 't?{history:{historyCursor:e.historyCursor??null,hasMoreHistory:e.hasMoreHistory??!1,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:e.latestLocalTurnOrdinal??null,addedItems:null,emptyPageStreak:0,pageCount:0,lastPageReason:null}}:{resetHistory:!0}';
if (!content.includes(target3)) throw new Error('target3 not found');
content = content.replace(target3, repl3);

// 4. snapshot history init branch 2 (missed in prior attempt)
const target3b = 'let e=a.history===void 0?nd:{historyCursor:a.history.historyCursor,hasMoreHistory:a.history.hasMoreHistory,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:a.history.latestLocalTurnOrdinal??null};';
const repl3b = 'let e=a.history===void 0?nd:{historyCursor:a.history.historyCursor,hasMoreHistory:a.history.hasMoreHistory,loading:!1,error:null,expanded:!1,latestLocalTurnOrdinal:a.history.latestLocalTurnOrdinal??null,addedItems:null,emptyPageStreak:0,pageCount:0,lastPageReason:null};';
if (!content.includes(target3b)) throw new Error('target3b not found');
content = content.replace(target3b, repl3b);

// 5. loaded page update with internal-item deduplication
const target4 = 'm=yield*se.withPermits(1)(pi(v,e=>rd(i,e.history)?C(e.data)||e.status===`deleted`?[{_tag:`noop`},{...e,history:nd}]:[{_tag:`loaded`},{...e,data:s(sd(e.data.value,u.items)),status:f&&p?`synchronizing`:e.status,history:f?cd(e.history,u):{...e.history,expanded:!0}}]:[{_tag:`noop`},e]));';
const repl4 = 'm=yield*se.withPermits(1)(pi(v,e=>{if(!rd(i,e.history))return[{_tag:`noop`},e];if(C(e.data)||e.status===`deleted`)return[{_tag:`noop`},{...e,history:nd}];let __ext=new Set(e.data.value.visibleTurnItems.map(ad)),__seen=new Set(__ext),__add=0;for(let r of u.items){let k=ad(r);if(!__seen.has(k)){__seen.add(k);__add++}}let __mrg=sd(e.data.value,u.items);return[{_tag:`loaded`},{...e,data:s(__mrg),status:f&&p?`synchronizing`:e.status,history:f?{...cd(e.history,u),addedItems:__add,emptyPageStreak:__add===0?(e.history.emptyPageStreak||0)+1:0,pageCount:(e.history.pageCount||0)+1,lastPageReason:t===void 0?`page`:`jump`}:{...e.history,expanded:!0,addedItems:__add,pageCount:(e.history.pageCount||0)+1,lastPageReason:`jump`}}]}));';
if (!content.includes(target4)) throw new Error('target4 not found');
content = content.replace(target4, repl4);

fs.writeFileSync('runtime-DL3Dibca.patched.js', content);
console.log('runtime-DL3Dibca.patched.js written successfully');
