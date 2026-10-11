const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {webcrypto} = require('node:crypto');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync(require('node:path').join(__dirname,'../../index.html'),'utf8');
const script = html.match(/<script>\n([\s\S]*?)\n<\/script>/)[1];
const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn,label) {
  for(let n=0;n<100;n++) { if(fn()) return; await wait(5); }
  throw new Error('Timeout: '+label);
}
async function fixture({persist=false,viewer=false}={}) {
  const dom=new JSDOM(html,{url:'http://localhost:8000',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document,requests=[];
  Object.defineProperty(w,'crypto',{value:webcrypto});
  w.TextEncoder=TextEncoder;
  w.matchMedia=()=>({matches:false});
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  const day=new Date().toISOString().slice(0,10),month=day.slice(0,7);
  const a='11111111-1111-4111-8111-111111111111',c='22222222-2222-4222-8222-222222222222',i='33333333-3333-4333-8333-333333333333';
  const family={id:'44444444-4444-4444-8444-444444444444',name:'Família principal',role:viewer?'viewer':'admin',currency:'BRL',timezone:'UTC'};
  const cat='55555555-5555-4555-8555-555555555555';
  const data={
    families:[family,{...family,id:'66666666-6666-4666-8666-666666666666',name:'Outra família'}],
    accounts:[{id:a,name:'Conta principal',kind:'checking',active:true,balance:'2000.01'},{id:'secondary',name:'Carteira',kind:'wallet',active:true,balance:'0.09'}],
    cards:[{id:c,name:'Cartão principal',last_four:'1234',active:true,credit_limit:'2000.00',used_limit:'300.00',available_limit:'1700.00',payment_account_id:a,closing_day:2,due_day:10}],
    categories:[{id:cat,name:'Compras',kind:'expense'},{id:'salary',name:'Salário',kind:'income'}],
    invoices:[{id:i,card_id:c,status:'closed',outstanding:'100.00',due_date:month+'-10'}, {id:'future',card_id:c,status:'open',outstanding:'200.00',due_date:'2099-12-10'}],
    transactions:[{id:'tx1',kind:'expense',status:'posted',amount:'300.00',category_id:cat,card_id:c,competency_date:day,description:'<img src=x onerror="alert(1)">'}],
    economic:{expense:'300.00',income:'0.00',categories:[{category_id:cat,kind:'expense',amount:'300.00'}]},
    failPurchase:false, unauthorized:false
  };
  w.fetch=async (url,options)=>{
    const u=new URL(url),body=options.body && options.headers['Content-Type']==='application/json'?JSON.parse(options.body):String(options.body || '');
    const req={path:u.pathname,query:u.searchParams,options,body};requests.push(req);
    const reply=(obj,status=200)=>({ok:status<400,status,json:async()=>JSON.parse(JSON.stringify(obj))});
    if(data.unauthorized && options.headers.Authorization) return reply({detail:'Sessão expirada.'},401);
    if(u.pathname==='/health') return reply({status:'ok'});
    if(u.pathname==='/auth/token') return reply({access_token:'test-token',token_type:'bearer'});
    if(u.pathname==='/auth/register') return reply({id:'user'});
    if(u.pathname==='/auth/me') return reply({id:'user',name:'Pessoa Teste',email:'pessoa@example.com'});
    if(u.pathname==='/auth/families') {
      if(options.method==='POST') { const created={...family,id:'new-family',name:body.name};data.families.push(created);return reply(created,201); }
      return reply(data.families);
    }
    if(u.pathname==='/reports/economic') return reply(data.economic);
    const key=u.pathname.slice(1);
    if(['accounts','cards','categories','invoices','transactions'].includes(key) && options.method==='GET') {
      const offset=Number(u.searchParams.get('offset') || 0),limit=Number(u.searchParams.get('limit') || 200);
      return reply(data[key].slice(offset,offset+limit));
    }
    if(u.pathname==='/cards/'+c+'/purchases') {
      if(data.failPurchase) { data.failPurchase=false; throw new w.TypeError('Network error'); }
      return reply({purchase:{id:'new-purchase'},installments:[],card:data.cards[0]},201);
    }
    if(u.pathname==='/invoices/'+i+'/payments') {
      data.invoices[0].outstanding='50.00';data.invoices[0].status='partially_paid';data.accounts[0].balance='1950.01';
      return reply({payment:{kind:'transfer'},invoice:data.invoices[0],card:data.cards[0],account_balance:'1950.01'},201);
    }
    if(u.pathname==='/transactions' && options.method==='POST') return reply({id:'cash'},201);
    if(['accounts','cards','categories'].includes(key) && options.method==='POST') return reply({id:'new-'+key,...body},201);
    throw new Error('Unmocked API path '+url);
  };
  if(persist) {w.localStorage.setItem('sisfin.token','test-token');w.localStorage.setItem('sisfin.sessionApiBase','http://localhost:8000');w.localStorage.setItem('sisfin.familyId',family.id);}
  w.eval(script);
  await wait(20);
  const click=(selector)=>{const target=d.querySelector(selector);assert.ok(target,selector);target.click();};
  const fill=(name,value)=>{ const e=d.querySelector(name);assert.ok(e,name);e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));};
  const submit=(selector)=>d.querySelector(selector).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  const ready=()=>until(()=>d.querySelector('#connection-status').textContent==='Atualizado','dashboard');
  async function login() {
    fill('#auth-email','pessoa@example.com');fill('#auth-password','testing-password-123');submit('#auth-form');
    await until(()=>!d.querySelector('#family-screen').hidden,'family picker');
    click('[data-action="select-family"]');await ready();
  }
  if(persist) await ready();
  return {w,d,requests,data,a,c,i,family,day,month,click,fill,submit,ready,login,close:()=>w.close()};
}

test('precisão monetária e centavos do parcelamento',async()=>{
  const f=await fixture();
  try {
    const m=f.w.SisFin;
    assert.equal(m.inputToCents('1.234,56'),123456n);
    assert.equal(m.inputToCents('150.00'),15000n);
    assert.equal(m.toDecimal(m.inputToCents('0,01')),'0.01');
    assert.equal(m.toDecimal(m.inputToCents('9.999.999.999,99')),'9999999999.99');
    assert.equal(m.money(200010n),'R$ 2.000,10');
    assert.deepEqual(Array.from(m.splitCents(10001n,3)),[3334n,3334n,3333n]);
    for(const value of ['1.2345','0,001','NaN','1e3','-1','0','10.000.000.000,00']) assert.throws(()=>m.inputToCents(value));
    assert.throws(()=>m.splitCents(1n,2));
    assert.throws(()=>m.decimalToCents(0.1));
  } finally {f.close();}
});

test('login, família, totais exatos e conteúdo escapado',async()=>{
  const f=await fixture();
  try {
    await f.login();
    assert.equal(f.w.localStorage.getItem('sisfin.token'),'test-token');
    assert.equal(f.w.localStorage.getItem('sisfin.familyId'),f.family.id);
    assert.equal(f.d.querySelector('#metric-balance').textContent,'R$ 2.000,10');
    assert.equal(f.d.querySelector('#metric-invoices').textContent,'R$ 100,00'); // exclui futuro
    assert.equal(f.d.querySelector('#metric-expense').textContent,'R$ 300,00');
    assert.equal(f.d.querySelectorAll('#recent-transactions img').length,0);
    assert.match(f.d.querySelector('#recent-transactions').textContent,/<img/);
    const auth=f.requests.find(r=>r.path==='/auth/token');
    assert.equal(auth.options.headers['Content-Type'],'application/x-www-form-urlencoded');
    assert.match(auth.body,/username=pessoa%40example.com/);
    for(const req of f.requests.filter(r=>!r.path.startsWith('/auth'))) {
      assert.equal(req.options.credentials,'omit');
      assert.equal(req.options.headers.Authorization,'Bearer test-token');
      assert.equal(req.options.headers['X-Family-ID'],f.family.id);
    }
    f.click('[data-action="navigate"][data-view="accounts"]');
    assert.equal(f.d.querySelector('#view-accounts').hidden,false);
    assert.equal(f.d.querySelectorAll('#invoices-list .invoice-row').length,2);
  } finally {f.close();}
});

test('compra parcelada: string decimal e mesma chave em nova tentativa incerta',async()=>{
  const f=await fixture({persist:true});
  try {
    f.click('[data-action="expense"]');
    f.fill('#f-amount','100,01');f.fill('#f-description','Compra parcelada');
    f.fill('#f-payment_method','credit');f.fill('#f-installments','3');
    assert.match(f.d.querySelector('#installment-preview').textContent,/2 × R\$ 33,34 \+ 1 × R\$ 33,33/);
    f.data.failPurchase=true;
    f.submit('#dialog-form');
    await until(()=>!f.d.querySelector('#dialog-error').hidden,'uncertain error');
    assert.match(f.d.querySelector('#dialog-error').textContent,/sem confirmação/);
    f.submit('#dialog-form');
    await until(()=>!f.d.querySelector('#dialog').open,'purchase success');
    const calls=f.requests.filter(r=>r.path.endsWith('/purchases'));
    assert.equal(calls.length,2);
    assert.equal(calls[0].body.amount,'100.01');assert.equal(calls[0].body.installments,3);
    assert.equal(calls[0].options.headers['Idempotency-Key'],calls[1].options.headers['Idempotency-Key']);
    assert.match(calls[0].options.headers['Idempotency-Key'],/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.equal(calls[0].options.headers['X-Family-ID'],f.family.id);
    assert.equal(calls[0].body.account_id,undefined);
    assert.equal(f.requests.filter(r=>r.path==='/transactions' && r.options.method==='POST').length,0);
  } finally {f.close();}
});

test('pagamento parcial chama apenas a rota de liquidação e atualiza o dashboard',async()=>{
  const f=await fixture({persist:true});
  try {
    f.click('[data-action="payment"]');
    f.fill('#f-amount','50,00');f.submit('#dialog-form');
    await until(()=>!f.d.querySelector('#dialog').open,'payment');
    await until(()=>f.d.querySelector('#metric-invoices').textContent==='R$ 50,00','updated invoice');
    const payment=f.requests.find(r=>r.path.endsWith('/payments'));
    assert.equal(payment.body.amount,'50.00');assert.equal(payment.body.account_id,f.a);
    assert.equal(payment.body.payment_method,'pix');
    assert.equal(payment.options.headers.Authorization,'Bearer test-token');
    assert.equal(f.requests.filter(r=>r.path==='/transactions' && r.options.method==='POST').length,0);
    assert.equal(f.d.querySelector('#metric-expense').textContent,'R$ 300,00');
    assert.equal(f.d.querySelector('#metric-balance').textContent,'R$ 1.950,10');
  } finally {f.close();}
});

test('despesa paga via PIX usa datas e valor do contrato de caixa',async()=>{
  const f=await fixture({persist:true});
  try {
    f.click('[data-action="expense"]');
    f.fill('#f-amount','150,00');f.fill('#f-description','Mercado');
    f.submit('#dialog-form');
    await until(()=>!f.d.querySelector('#dialog').open,'cash transaction');
    const req=f.requests.find(r=>r.path==='/transactions' && r.options.method==='POST');
    assert.equal(req.body.amount,'150.00');assert.equal(req.body.kind,'expense');assert.equal(req.body.payment_method,'pix');
    assert.equal(req.body.payment_date,f.day);assert.equal(req.body.competency_date,f.day);
    assert.equal(req.body.installments,undefined);
  } finally {f.close();}
});

test('trocar servidor remove a sessão e não envia JWT ao healthcheck configurado',async()=>{
  const f=await fixture({persist:true});
  try {
    f.click('#app-shell [data-action="config"]');
    f.fill('#f-api_url','https://other-api.onrender.com');
    f.click('[data-action="test-api"]');
    await until(()=>f.d.querySelector('#connection-test-result').textContent.includes('API respondeu'),'health');
    const health=f.requests.find(r=>r.path==='/health');
    assert.equal(health.options.headers.Authorization,undefined);
    assert.equal(health.options.headers['X-Family-ID'],undefined);
    f.submit('#dialog-form');await until(()=>!f.d.querySelector('#dialog').open,'config');
    assert.equal(f.w.localStorage.getItem('sisfin.token'),null);
    assert.equal(f.w.localStorage.getItem('sisfin.familyId'),null);
    assert.equal(f.w.localStorage.getItem('sisfin.apiBaseUrl'),'https://other-api.onrender.com');
    assert.equal(f.d.querySelector('#app-shell').hidden,true);
  } finally {f.close();}
});

test('visualizador não pode abrir formulários financeiros',async()=>{
  const f=await fixture({persist:true,viewer:true});
  try {
    assert.equal(f.d.querySelector('#viewer-note').hidden,false);
    for(const button of f.d.querySelectorAll('[data-write]')) assert.equal(button.disabled,true);
  } finally {f.close();}
});

test('resposta 401 limpa os dados financeiros e retorna ao login',async()=>{
  const f=await fixture({persist:true});
  try {
    f.data.unauthorized=true;
    f.click('[data-action="refresh"]');
    await until(()=>f.d.querySelector('#app-shell').hidden,'session expired');
    assert.equal(f.w.localStorage.getItem('sisfin.token'),null);
    assert.equal(f.d.querySelector('#metric-balance').textContent,'—');
    assert.equal(f.d.querySelector('#login-screen').hidden,false);
  } finally {f.close();}
});

test('saldos consolidam todas as páginas, sem truncamento em 200 contas',async()=>{
  const f=await fixture();
  try {
    f.data.accounts=Array.from({length:201},(_,n)=>({id:'a'+n,name:'Conta '+n,kind:'wallet',active:true,balance:'0.01'}));
    await f.login();
    assert.equal(f.d.querySelector('#metric-balance').textContent,'R$ 2,01');
    assert.ok(f.requests.find(r=>r.path==='/accounts' && r.query.get('offset')==='200'));
  } finally {f.close();}
});

test('créditos líquidos não produzem fatias negativas no gráfico',async()=>{
  const f=await fixture();
  try {
    f.data.economic.expense='290.00';
    f.data.economic.categories.push({category_id:'refund',kind:'expense',amount:'-10.00'});
    await f.login();
    assert.equal(f.d.querySelector('#metric-expense').textContent,'R$ 290,00');
    assert.match(f.d.querySelector('#chart-content').textContent,/Créditos líquidos por categoria/);
    assert.equal(f.d.querySelectorAll('.category-line').length,1);
  } finally {f.close();}
});

test('nova família é selecionada usando o resultado do POST confirmado',async()=>{
  const f=await fixture({persist:true});
  try {
    f.click('[data-action="new-family"]');
    f.fill('#f-name','Família nova');f.fill('#f-timezone','UTC');
    f.submit('#dialog-form');
    await until(()=>f.w.localStorage.getItem('sisfin.familyId')==='new-family','new family');
    const call=f.requests.find(r=>r.path==='/auth/families' && r.options.method==='POST');
    assert.equal(call.body.name,'Família nova');
    assert.ok(call.options.headers['Idempotency-Key']);
    assert.equal(call.options.headers.Authorization,'Bearer test-token');
  } finally {f.close();}
});
