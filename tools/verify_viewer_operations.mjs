/** Real HTTP, encrypted-store and Chromium regressions. No credentials are persisted. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const out = resolve(root, 'artifacts/operational-fixes/viewer');
mkdirSync(out, {recursive:true});
const { chromium } = await import(pathToFileURL(resolve(root, 'workbench/ui/node_modules/playwright/index.mjs')));
const child = spawn(process.env.PYTHON || 'python', ['-u', resolve(root, 'tools/viewer_qa_fixture.py')], {cwd:root, stdio:['pipe','pipe','pipe']});
child.stderr.on('data', () => {});
const lines = createInterface({input:child.stdout});
const buffered = [], pending = [];
lines.on('line', line => { const value=JSON.parse(line); if(pending.length) pending.shift()(value); else buffered.push(value); });
const receive = () => buffered.length ? Promise.resolve(buffered.shift()) : new Promise((resolve,reject) => {
  const timeout=setTimeout(()=>reject(new Error('Fixture timed out')),300000);
  pending.push(value=>{clearTimeout(timeout);resolve(value);});
});
const rpc = async value => { child.stdin.write(JSON.stringify(value)+'\n'); const answer=await receive(); assert.ok(!answer.error,'QA fixture control failed'); return answer; };
const childExited=new Promise(resolve=>child.once('exit',resolve));
const checks=[];
let browser,currentPage,phase='startup';
const check=(id,data={})=>checks.push({id,verdict:'PASS',...data});
try {
  const {url}=await receive();
  browser=await chromium.launch();
  for(const [viewport,options] of [['desktop',{viewport:{width:1440,height:1000}}],['mobile',{viewport:{width:390,height:844},isMobile:true,hasTouch:true}]]) {
    phase=viewport+' base';
    const context=await browser.newContext(options),page=await context.newPage();
    currentPage=page; page.setDefaultTimeout(10000);
    let requests=0,unexpected=0,pageErrors=0;
    page.on('pageerror',()=>pageErrors++);
    page.on('request',r=>{requests++;if(r.method()!=='GET'||new URL(r.url()).origin!==new URL(url).origin)unexpected++;});
    const connect=async token=>{await page.goto(url);await page.getByLabel('Viewer key',{exact:true}).fill(token);await page.getByRole('button',{name:'Connect viewer',exact:true}).click();};
    const settled=()=>page.waitForFunction(()=>document.querySelector('.workspace-bar')?.getAttribute('aria-busy')==='false'||document.querySelector('#viewer-key:not([disabled])'));
    const refreshed=async()=>{await page.getByRole('button',{name:'Refresh view',exact:true}).click();await settled();};
    const receiptSection=page.locator('section[aria-labelledby="receipts-title"]');
    const sequences=()=>receiptSection.locator('[data-receipt-sequence]').evaluateAll(items=>items.map(item=>Number(item.dataset.receiptSequence)));
    const receipts=async name=>{await page.getByRole('button',{name,exact:true}).click();await settled();};

    phase=viewport+' wrong token';
    await connect('am1.synthetic.invalid');await page.getByRole('alert').waitFor();
    assert.equal(await page.locator('#workspace-title').count(),0);assert.equal(await page.locator('#viewer-key').inputValue(),'');
    check(viewport+'_wrong_token_clears');
    const agent=await rpc({action:'issue',role:'agent'});
    await connect(agent.token);await page.getByRole('alert').waitFor();
    assert.ok((await page.getByRole('alert').innerText()).includes('Agent credentials'));
    assert.equal(await page.locator('#workspace-title').count(),0);check(viewport+'_wrong_role_refused');

    phase=viewport+' remaining budget';
    const limited=await rpc({action:'issue',uses:1});
    await connect(limited.token);await page.locator('#workspace-title').waitFor();
    await page.getByText("About this viewer's access",{exact:true}).click();
    const shown=await page.locator('dt').filter({hasText:'Remaining uses'}).locator('xpath=following-sibling::dd[1]').innerText();
    const actual=(await rpc({action:'state',token:limited.token})).capability.result.remaining_uses;
    assert.equal(actual,0);assert.equal(shown,String(actual));
    check(viewport+'_QA007_budget_matches_completed_read',{displayed:Number(shown),backend:actual});
    const budgetAlert=await page.getByRole('alert').innerText();
    assert.ok(budgetAlert.includes('no workspace reads left')&&budgetAlert.includes('renewed viewer key'));
    assert.ok(!budgetAlert.includes('Try refreshing'));
    assert.equal(await page.getByRole('button',{name:'Refresh view',exact:true}).isDisabled(),true);
    await page.getByRole('alert').scrollIntoViewIfNeeded();
    await page.screenshot({path:resolve(out,viewport+'-budget-renewal.png'),fullPage:false});
    await receipts('Newest receipts');
    assert.equal((await rpc({action:'state',token:limited.token})).capability.result.remaining_uses,0);
    check(viewport+'_QA003_exhausted_key_receipt_metadata_remains_readable');
    await page.getByRole('button',{name:'Connect a new viewer key',exact:true}).click();
    assert.equal(await page.locator('#workspace-title').count(),0);assert.equal(await page.locator('#viewer-key').inputValue(),'');
    const renewed=await rpc({action:'issue',uses:3});
    await page.getByLabel('Viewer key',{exact:true}).fill(renewed.token);
    await page.getByRole('button',{name:'Connect viewer',exact:true}).click();await page.locator('#workspace-title').waitFor();
    assert.equal(await page.getByRole('alert').count(),0);check(viewport+'_QA003_terminal_budget_clear_renewal_path');

    phase=viewport+' last-use race';
    const raced=await rpc({action:'issue',uses:1});await rpc({action:'delay',seconds:1});
    const workspaceRequested=page.waitForRequest(r=>new URL(r.url()).pathname==='/v1/workspace');
    await connect(raced.token);await workspaceRequested;await rpc({action:'spend',token:raced.token});
    await page.getByRole('alert').waitFor();
    assert.ok((await page.getByRole('alert').innerText()).includes('no workspace reads left'));
    assert.equal(await page.locator('#workspace-title').count(),0);assert.equal(await page.locator('#viewer-key').inputValue(),'');
    await rpc({action:'delay',seconds:0});check(viewport+'_QA003_actual_429_after_preflight_is_actionable');

    phase=viewport+' expiry and revoke';
    const short=await rpc({action:'issue',ttl:60});await connect(short.token);await page.locator('#workspace-title').waitFor();
    await rpc({action:'advance',seconds:61});await refreshed();await page.locator('#viewer-key').waitFor();
    assert.equal(await page.locator('#workspace-title').count(),0);check(viewport+'_expired_refresh_clears_data');
    const revoked=await rpc({action:'issue'});await connect(revoked.token);await page.locator('#workspace-title').waitFor();
    await rpc({action:'revoke',id:revoked.id});await refreshed();await page.locator('#viewer-key').waitFor();
    assert.equal(await page.locator('#workspace-title').count(),0);check(viewport+'_revoked_refresh_clears_data');

    phase=viewport+' outage and reconnect';
    const normal=await rpc({action:'issue'});await connect(normal.token);await page.locator('#workspace-title').waitFor();
    const oldTitle=await page.locator('.brief-title').innerText();
    await rpc({action:'stop'});await refreshed();await page.getByRole('alert').waitFor();
    assert.ok((await page.getByRole('alert').innerText()).includes('may be older'));assert.equal(await page.locator('.brief-title').innerText(),oldTitle);
    await rpc({action:'start'});await refreshed();assert.equal(await page.getByRole('alert').count(),0);
    check(viewport+'_outage_marks_stale_restart_refresh_recovers');

    phase=viewport+' late response';
    await rpc({action:'delay',seconds:1});
    const lateRequest=page.waitForRequest(r=>new URL(r.url()).pathname==='/v1/workspace');
    await page.getByRole('button',{name:'Refresh view',exact:true}).click();await lateRequest;
    await page.getByRole('button',{name:'Disconnect',exact:true}).click();await page.waitForTimeout(1300);
    assert.equal(await page.locator('#workspace-title').count(),0);assert.equal(await page.locator('#viewer-key').inputValue(),'');
    await rpc({action:'delay',seconds:0});check(viewport+'_late_refresh_cannot_restore_disconnected_data');

    phase=viewport+' inert maximum text';
    const markup='<img src=x onerror=window.__qaExecuted=1> ';
    const title=markup+'A'.repeat(90-markup.length),outcome='B'.repeat(400);
    await rpc({action:'title',value:title,outcome});await connect(normal.token);await page.locator('#workspace-title').waitFor();
    assert.equal(await page.locator('.brief-title').innerText(),title);
    assert.equal(await page.locator('section[aria-labelledby="brief-title"] .lead').innerText(),outcome);
    assert.equal(await page.locator('img').count(),0);assert.equal(await page.evaluate(()=>window.__qaExecuted??null),null);
    const geometry=await page.evaluate(()=>({scroll_width:document.documentElement.scrollWidth,viewport_width:document.documentElement.clientWidth}));
    assert.equal(geometry.scroll_width,geometry.viewport_width);
    await page.locator('#brief-title').scrollIntoViewIfNeeded();await page.screenshot({path:resolve(out,viewport+'-max-text.png'),fullPage:false});
    check(viewport+'_QA006_max_90_title_400_outcome_wrap_and_stay_inert',geometry);

    phase=viewport+' latest receipts';
    await rpc({action:'seed_receipts',count:65});await refreshed();await receipts('Newest receipts');
    assert.ok((await receiptSection.innerText()).includes('SCOPE REQUIRED'));
    const newest=await rpc({action:'state',token:normal.token}),firstSequences=await sequences();
    assert.equal(firstSequences.length,50);assert.deepEqual(firstSequences,newest.receipts.result.items.map(item=>item.sequence));assert.ok(newest.receipts.result.has_more);
    await receipts('Older receipts');
    const older=await rpc({action:'state',token:normal.token,before:newest.receipts.result.next_before}),olderSequences=await sequences();
    assert.deepEqual(olderSequences,older.receipts.result.items.map(item=>item.sequence));
    assert.ok(olderSequences.length>0&&olderSequences.length<=50);assert.ok(olderSequences.every(seq=>seq<Math.min(...firstSequences)));
    assert.equal(older.capability.result.remaining_uses,newest.capability.result.remaining_uses);
    await receipts('Newest receipts');assert.deepEqual(await sequences(),firstSequences);
    await page.locator('#receipts-title').scrollIntoViewIfNeeded();await page.screenshot({path:resolve(out,viewport+'-latest-receipts.png'),fullPage:false});
    check(viewport+'_QA002_latest_refusal_and_exclusive_pagination',{first_page:50,older_page:olderSequences.length});

    phase=viewport+' retention';
    // Exercise the real 2,000-receipt retention boundary, not a mocked response.
    if(viewport==='desktop')await rpc({action:'seed_receipts',count:2005});
    await receipts('Newest receipts');const retained=await rpc({action:'state',token:normal.token});
    assert.equal(retained.receipts.result.history_truncated,true);assert.equal(retained.receipts.result.retention_limit,2000);
    assert.ok((await receiptSection.locator('.retention-note').innerText()).includes('Older history has been removed'));assert.equal((await sequences()).length,50);
    await page.locator('#receipts-title').scrollIntoViewIfNeeded();await page.screenshot({path:resolve(out,viewport+'-retention.png'),fullPage:false});
    check(viewport+'_QA002_real_retention_truncation_visible',{retention_limit:2000});

    phase=viewport+' read-only and privacy';
    assert.deepEqual(await page.getByRole('button').allTextContents(),['Refresh view','Disconnect','Newest receipts','Older receipts']);
    assert.equal(await page.locator('select,textarea,input').count(),0);assert.ok(!(await page.content()).includes(normal.token));assert.ok(!page.url().includes(normal.token));
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    await page.goto(new URL('/style.css',url).href);await page.goBack();await page.locator('#viewer-key').waitFor();
    assert.equal(await page.locator('#workspace-title').count(),0);assert.equal(await page.locator('#viewer-key').inputValue(),'');
    assert.equal(unexpected,0);assert.equal(pageErrors,0);
    check(viewport+'_read_only_gets_no_storage_or_history_credentials',{requests});await context.close();
  }
  const evidence={success:true,source:'real local HTTP runtime, encrypted SQLite and Chromium',findings_fixed:['QA-002','QA-003','QA-006','QA-007'],checks};
  writeFileSync(resolve(out,'results.json'),JSON.stringify(evidence,null,2)+'\n');
  console.log(JSON.stringify({passed_checks:checks.length,findings_fixed:evidence.findings_fixed,evidence:'artifacts/operational-fixes/viewer/results.json'}));
} catch(error) {
  if(currentPage&&!currentPage.isClosed())await currentPage.screenshot({path:resolve(out,'diagnostic-failure.png'),mask:[currentPage.locator('#viewer-key')]}).catch(()=>{});
  writeFileSync(resolve(out,'diagnostic-failure.json'),JSON.stringify({phase,error_name:error.name,checks},null,2)+'\n');
  console.error('Viewer regression stopped at '+phase+' ('+error.name+'); credential-bearing traces suppressed.');process.exitCode=1;
} finally {
  if(browser)await browser.close();
  if(child.exitCode===null){try{await rpc({action:'exit'});}catch{} const timeout=setTimeout(()=>child.kill(),10000);await childExited;clearTimeout(timeout);}
}
