'use strict';

const byId = id => document.getElementById(id);
const apiRoot = '/api/experiments/knowledge/';
const studyKey = 'photonguard.knowledge.study';
const typeLabel = {THEORY_ONLY:'理论',FORMULA_EXAMPLE:'公式与示例',EXPERIMENT_READY:'可在工作台验证'};
const answerTitle = {LIVE_AI:'AI 讲解',LIVE_AI_GENERAL:'DeepSeek 通识讲解',INJECTED_AI_GENERAL:'测试模型通识讲解',
  LOCAL_AFTER_AI_REVIEW:'AI 暂未完成讲解',GENERAL_AFTER_AI_FAILURE:'AI 暂未回复'};
let overview, connection, topic = '', page = 1, active = '', requestNumber = 0, articleNumber = 0;
let previous = [], known = [], focus = '', studyPath = '', studyVersion = '';
let sessionId = '', lastTurn = '', lesson = '', lessonSection = '', selectedText = '';
let experimentId = '';
let resumeTurn = '';
const claimLabel = {DIRECT:'原文支持',DERIVED:'由资料推导',WORKED_EXAMPLE:'练习示例',UNVERIFIED:'待补依据'};

function el(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function link(text, href, external = false) {
  const item = el('a', text); item.href = href;
  if (external) { item.target = '_blank'; item.rel = 'noopener noreferrer'; }
  return item;
}

async function api(path, body) {
  const response = await fetch(apiRoot + path, body ? {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':connection.csrf},body:JSON.stringify(body)} : {});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '读取未完成，请重试。');
  return value;
}

function error(target, err) {
  const message = el('p', err.message, 'notice'); message.setAttribute('role','alert');
  target.replaceChildren(message);
}

function labelFor(item) {
  return `${overview.levels[item.level]} · ${typeLabel[item.capability]}`;
}

function articleLink(item) {
  return link(item.title, '#article=' + encodeURIComponent(item.id));
}

function showNavigation() {
  const packFilter=byId('pack'), selected=packFilter.value;
  const options=overview.packs.map(pack=>{
    const option=el('option',pack.title);option.value=pack.id;return option;
  });
  const all=el('option','全部知识包');all.value='';
  packFilter.replaceChildren(all,...options);
  if(options.some(option=>option.value===selected))packFilter.value=selected;
  const topics = byId('topics');
  const choices = [{id:'',title:'全部主题',count:overview.published}, ...overview.topics];
  topics.replaceChildren(...choices.map(item => {
    const button = el('button', `${item.title} · ${item.count}`);
    button.type='button'; button.setAttribute('aria-pressed',String(topic===item.id));
    button.onclick=()=>{topic=item.id;page=1;showNavigation();search();};
    return button;
  }));
  byId('paths').replaceChildren(...overview.paths.map(item => {
    const button=el('button',item.title);button.type='button';button.onclick=()=>showPath(item);
    return button;
  }));
  byId('study-topics').replaceChildren(...overview.study_topics.map(item =>
    link(item.title, '#topic=' + encodeURIComponent(item.id))));
}

async function search() {
  const ticket=++requestNumber;
  const parameters=new URLSearchParams({q:byId('query').value,topic,page:String(page),level:byId('level').value,
    license:byId('license').value,capability:byId('capability').value,pack:byId('pack').value});
  byId('search-status').textContent='正在搜索…';
  try {
    const results=await api('search?'+parameters);
    if (ticket!==requestNumber) return;
    if (results.catalog_version!==overview.catalog_version) {
      overview=await api('overview');showNavigation();showSummary();
    }
    const topicResults=byId('topic-results');
    topicResults.hidden=!results.study_topics.length;
    topicResults.replaceChildren();
    if(results.study_topics.length){
      topicResults.append(el('h3','相关专题'),list(results.study_topics,item=>
        link(item.title,'#topic='+encodeURIComponent(item.id))));
    }
    byId('search-status').textContent=results.total ? `找到 ${results.total} 篇条目` :
      results.study_topics.length ? '可以从相关专题开始。' : '未找到对应条目。可以换个术语或浏览主题。';
    byId('results').replaceChildren(...results.items.map(item=>{
      const button=el('button',null,'result-item');button.type='button';button.dataset.article=item.id;
      if(active===item.id)button.setAttribute('aria-current','true');
      button.append(el('strong',item.title),el('p',item.definition),el('small',labelFor(item)+' · '+item.source,'muted'));
      button.onclick=()=>{if(location.hash==='#article='+item.id)showArticle(item.id);else location.hash='article='+item.id;};
      return button;
    }));
    const pages=Math.max(1,Math.ceil(results.total/results.limit));
    byId('pagination').hidden=pages<=1;byId('page-count').textContent=`${page} / ${pages}`;
    byId('previous').disabled=page===1;byId('next').disabled=page>=pages;
  } catch(err) { if(ticket===requestNumber){byId('search-status').textContent='';byId('topic-results').hidden=true;error(byId('results'),err);} }
}

function list(items, render) {
  const result=el('ul',null,'links');
  for(const item of items){const row=el('li');row.append(render(item));result.append(row);}
  return result;
}

function section(target, title, text, className) {
  if(text)target.append(el('h3',title),el('p',text,className));
}

function showConventions(target, items) {
  if(!items?.length)return;
  const conventions=el('details');
  conventions.append(el('summary','符号与单位约定'));
  for(const item of items){
    conventions.append(el('h3',item.title),el('p',item.use),el('p',item.convert,'muted'));
  }
  target.append(conventions);
}

async function showArticle(ident) {
  const ticket=++articleNumber;
  const target=byId('article');
  try {
    const item=await api('articles/'+encodeURIComponent(ident));
    if(ticket!==articleNumber)return;
    active=item.id;
    byId('results').querySelectorAll('[data-article]').forEach(button=>{
      if(button.dataset.article===active)button.setAttribute('aria-current','true');else button.removeAttribute('aria-current');
    });
    target.replaceChildren(el('p',labelFor(item),'muted'),el('h2',item.title),el('p',item.definition,'definition'),el('p',item.body));
    section(target,'公式',item.formula,'formula');
    if(item.formula && item.variables)target.append(el('p',item.variables,'muted'));
    section(target,'适用条件',item.conditions.join(' · '));
    showConventions(target,item.conventions);
    section(target,'例子或观察',item.example);
    section(target,'容易混淆的地方',item.caution);
    if(item.prerequisite_links.length)target.append(el('h3','前置知识'),list(item.prerequisite_links,articleLink));
    if(item.related_links.length)target.append(el('h3','相关条目'),list(item.related_links,articleLink));
    const sources=el('details');sources.append(el('summary','来源与许可'),el('p',`${overview.licenses[item.license]} · ${item.pack} ${item.version}`));
    for(const source of item.sources){
      sources.append(link(source.title,source.url,true),el('p',`${source.section} · ${source.license}`,'muted'));
      if(source.author && source.edition)sources.append(el('p',`${source.author} · ${source.edition}`,'muted'));
    }
    target.append(sources);
    if(item.claims?.length){
      const claims=el('details');claims.append(el('summary','核心结论与依据'));
      for(const claim of item.claims){
        claims.append(el('p',`${claimLabel[claim.kind]} · ${claim.statement}`));
        for(const index of claim.source_indices){
          const source=item.sources[index];if(source)claims.append(link(source.section,source.url,true));
        }
      }
      target.append(claims);
    }
    const ask=el('button','围绕这篇提问','primary');ask.type='button';
    ask.onclick=()=>{reset();focus=item.id;studyVersion=item.catalog_version;byId('question').value=`请讲解${item.title}`;studyContext(item.title);location.hash='assistant';};
    const actions=el('div',null,'actions');actions.append(ask);
    if(item.experiment && !connection.standalone)actions.append(link('到工作台查看 · '+item.experiment.label,item.experiment.url));
    target.append(actions);
    if(matchMedia('(max-width:620px)').matches)target.focus();
  } catch(err){if(ticket===articleNumber)error(target,err);}
}

async function showTopic(ident) {
  const ticket=++articleNumber;
  const target=byId('article');
  try{
    const item=await api('topics/'+encodeURIComponent(ident));
    if(ticket!==articleNumber)return;
    target.replaceChildren(el('h2',item.title),el('p',item.intro,'definition'));
    showConventions(target,item.conventions);
    if(item.links.length)target.append(el('h3','前置知识'),list(item.links,articleLink));
    const openSection=(part,question,quote='')=>{
      reset();lesson=item.id;lessonSection=part.id;studyVersion=item.catalog_version;
      selectedText=quote;
      studyContext(item.title+' · '+part.title+(quote?' · 已引用选中文字':''));
      byId('question').value=question;
      location.hash='assistant';
    };
    const figure=knowledgeFigure(item.id,(id,question)=>{
      const part=item.sections.find(value=>value.id===id);
      if(part)openSection(part,question);
    },item.figure);
    if(figure)target.append(figure);
    for(const part of item.sections){
      const block=el('section',null,'topic-part');
      block.append(el('h3',part.title),el('p',part.text));
      if(part.formula)block.append(el('p',part.formula,'formula'));
      if(part.conditions)block.append(el('p',part.conditions,'muted'));
      block.append(list(part.references,id=>articleLink({id,title:item.reference_titles[id]})));
      const ask=el('button','问这一节');ask.type='button';
      ask.onclick=()=>{
        const selection=window.getSelection();
        const selected=selection && selection.rangeCount &&
          block.contains(selection.anchorNode) && block.contains(selection.focusNode) ?
          selection.toString().trim().slice(0,240) : '';
        const fragment=selected.replace(/\s+/g,' ');
        const quote=fragment && [part.title,part.text,part.formula,part.conditions].some(
          value=>value?.replace(/\s+/g,' ').includes(fragment)) ? fragment : '';
        openSection(part,quote ? '请讲解这句：'+quote : '请讲解'+part.title,quote);
      };
      block.append(ask);target.append(block);
    }
    if(matchMedia('(max-width:620px)').matches)target.focus();
  }catch(err){if(ticket===articleNumber)error(target,err);}
}

function showPath(item) {
  ++articleNumber;
  const target=byId('article');target.replaceChildren(el('h2',item.title));
  if(item.lesson){
    const lesson=overview.study_topics.find(topic=>topic.id===item.lesson);
    target.append(el('p',lesson.intro),link('先读专题 · '+lesson.title,'#topic='+lesson.id));
  }
  const order=el('ol',null,'learning-order');
  for(const article of item.entries){
    const li=el('li');const label=el('label');const check=el('input');check.type='checkbox';check.value=article.id;
    label.append(check,' 已掌握');li.append(articleLink(article),' ',label);order.append(li);
  }
  const ask=el('button','按这条路径学习','primary');ask.type='button';
  ask.onclick=()=>{
    const selected=[...order.querySelectorAll('input:checked')].map(control=>control.value);
    reset();known=selected;studyPath=item.id;studyVersion=overview.catalog_version;
    byId('question').value=`我想${item.title}，请按知识关系讲解。`;studyContext(item.title);location.hash='assistant';
  };
  target.append(order,ask);
}

function studyContext(text) {
  byId('study-context').textContent=text;
  byId('study-context').hidden=!text;
}

function reset() {
  previous=[];known=[];focus='';studyPath='';studyVersion='';
  sessionId='';lastTurn='';lesson='';lessonSection='';selectedText='';
  experimentId='';
  resumeTurn='';
  sessionStorage.removeItem(studyKey);
  byId('question').value='';studyContext('');
  byId('answer').replaceChildren(el('h2','从一个问题开始'),el('p','也可以先打开条目，再选择「围绕这篇提问」。'));
}

function renderAnswer(result) {
  const target=byId('answer');
  const current=result.catalog_version===overview.catalog_version;
  const referenceLink=item=>current?articleLink(item):el('span',item.title);
  const refs=new Map((result.references||[]).map(item=>[item.id,item]));
  const citationOrder=[...refs.keys()];
  const aiAnswer=['LIVE_AI','LIVE_AI_GENERAL','INJECTED_AI','INJECTED_AI_GENERAL'].includes(result.mode);
  function renderPart(parent,part,showSources=true){
    const sources=el('div',null,'references');
    if(showSources)for(const ident of part.references)if(refs.has(ident))sources.append(referenceLink(refs.get(ident)));
    const paragraph=window.photonStudyText(el('p'),part.text);
    if(!showSources)for(const id of part.references.filter(id=>refs.has(id))){
      const jump=el('button',`[${citationOrder.indexOf(id)+1}]`,'text-button');jump.type='button';
      jump.setAttribute('aria-label','查看引用 '+refs.get(id).title);
      jump.onclick=()=>{
        const entry=document.getElementById('study-ref-'+id);
        if(entry){entry.parentElement.open=true;entry.open=true;entry.scrollIntoView({block:'nearest'});entry.querySelector('summary').focus();}
      };
      paragraph.append(' ',jump);
    }
    parent.append(el('h3',part.heading),paragraph);
    if(sources.childElementCount)parent.append(sources);
  }
  target.replaceChildren(el('h2',answerTitle[result.mode]||'本地学习依据'),
    el('p',result.question,'muted'));
  if(aiAnswer){
    if(result.answer_review && result.answer_review.accepted === false)
      target.append(el('p','本次答复核对未完成，以下为已保存的初稿。','notice'));
    const explanation=el('div',null,'study-response');
    for(const part of result.sections)renderPart(explanation,part,false);
    target.append(explanation);
  }
  const evidenceStart=target.childElementCount;
  if(!current)target.append(el('p','这轮引用来自旧版知识库，以下为当时保存的资料。可开启新问题查询当前版本。','notice'));
  if(result.message)target.append(el('p',result.message,'notice'));
  if(result.experiment)target.append(experimentEvidence(result.experiment));
  if(result.imported_result && !connection.standalone)target.append(link('查看来源实验结果',
    '/experiments#case='+encodeURIComponent(result.imported_result.record_id)));
  if(result.session){
    const save=el('button','下载学习摘要','text-button');save.type='button';
    save.onclick=async()=>{
      try {
        const data=await api('sessions/'+encodeURIComponent(result.session.id)+'/summary');
        const lines=['# PhotonGuard 学习摘要','',`知识库版本 ${data.catalog_version}`];
        for(const turn of data.turns){
          lines.push('','## '+turn.question.replace(/\s+/g,' '),'',
            answerTitle[turn.mode]||'本地学习依据');
          if(turn.selection)lines.push('','所选原句：'+turn.selection);
          for(const item of turn.conventions||[])lines.push('',item.title,item.use,item.convert);
          if(turn.active_convention)lines.push('',turn.active_convention);
          if(turn.message)lines.push('',turn.message);
          if(turn.answer_review && turn.answer_review.accepted === false)
            lines.push('','本次答复核对未完成，以下为已保存的初稿。');
          if(turn.calculation){
            const calc=turn.calculation;
            if(turn.previous_calculation)lines.push('','沿用或修改前：'+turn.previous_calculation.result);
            lines.push('',calc.title,calc.formula,calc.result,'适用条件：'+calc.condition);
          }
          if(turn.experiment)lines.push('',...experimentLines(turn.experiment));
          if(turn.status==='NO_MATCH')lines.push('','待补资料或另行查证。');
          for(const part of turn.sections)lines.push('','### '+part.heading,'',part.text);
          for(const ref of turn.references){
            lines.push('',`- ${ref.title}（${ref.id} · ${ref.pack} ${ref.version}）`);
            for(const source of ref.sources||[])lines.push(`  - ${source.section} [${source.title}](${source.url})`);
          }
        }
        const url=URL.createObjectURL(new Blob([lines.join('\n')+'\n'],{type:'text/markdown;charset=utf-8'}));
        const download=link('下载学习摘要',url);
        download.download='PhotonGuard-学习摘要.md';download.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch(err){target.append(el('p',err.message,'notice'));}
    };
    target.append(save);
  }
  if(result.session?.turns.length>1){
    const turns=el('details');turns.append(el('summary','最近的学习问题'),
      list(result.session.turns,item=>{
        const button=el('button',item.question,'text-button');button.type='button';
        button.onclick=async()=>{
          try{
            const saved=await api('sessions/'+result.session.id+'/turns/'+item.id);
            if(sessionId!==result.session.id)return;
            resumeTurn=item.id;previous=saved.direct||[];
            renderAnswer(saved);studyContext('正在回看这轮。可接着这轮继续提问。');
          }catch(err){error(target,err);}
        };
        return button;
      }));
    target.append(turns);
  }
  if(result.status==='AMBIGUOUS'){
    target.append(list(result.choices,referenceLink));return;
  }
  if(result.status==='NO_MATCH'){
    target.append(link('回知识库浏览主题','/knowledge'));return;
  }
  if(result.calculation){
    const calc=result.calculation, card=el('section',null,'calculation');
    card.append(el('h3',calc.title),el('p',calc.formula,'formula'));
    if(!result.sections.some(part=>part.text===calc.result))card.append(el('p',calc.result));
    card.append(el('p',calc.condition,'muted'));
    target.append(card);
  }
  if(result.active_convention)target.append(el('p',result.active_convention,'notice'));
  showConventions(target,result.conventions);
  if(!aiAnswer)for(const part of result.sections)renderPart(target,part);
  if(result.optional_section){
    const extra=el('details');
    extra.append(el('summary','延伸阅读'));
    renderPart(extra,result.optional_section);
    target.append(extra);
  }
  const lessonRoots=result.direct?.slice(0,2)||[];
  const connectedPrerequisites=new Set(lessonRoots.flatMap(id=>refs.get(id)?.prerequisites||[]));
  const nextItems=result.learning_order.filter(id=>connectedPrerequisites.has(id)&&!result.direct?.includes(id));
  if(nextItems.length){
    target.append(el('h3','接下来怎么学'));
    const order=el('ol',null,'learning-order');
    for(const id of nextItems)if(refs.has(id)){
      const item=refs.get(id), row=el('li');row.append(referenceLink(item));order.append(row);
    }
    target.append(order);
  }
  const cited=new Set(result.sections.flatMap(part=>part.references));
  const sourcePanel=el('details');sourcePanel.append(el('summary','本轮条目、公式与出处'));
  for(const item of [...result.references].sort((a,b)=>Number(cited.has(b.id))-Number(cited.has(a.id)))){
    const record=el('details',null,'ref-entry');record.id='study-ref-'+item.id;
    record.append(el('summary',`[${citationOrder.indexOf(item.id)+1}] ${item.title}`),
      el('p',`${item.pack} ${item.version}`,'muted'),el('p',item.definition));
    if(item.formula)record.append(el('p',item.formula,'formula'));
    if(item.variables)record.append(el('p',item.variables,'muted'));
    section(record,'适用条件',item.conditions.join(' · '));
    for(const source of item.sources)record.append(link(source.title,source.url,true),el('p',source.section,'muted'));
    sourcePanel.append(record);
  }
  if(result.references.length)target.append(sourcePanel);
  if(aiAnswer && result.references.length){
    const evidence=el('div',null,'study-evidence');
    evidence.append(...Array.from(target.children).slice(evidenceStart));
    target.append(evidence);
  }
  if(result.status==='FOUND' && current && result.session && !result.experiment){
    if(!connection.standalone)renderHandoff(target,result);
    else if(result.calculation?.id==='single-slit' && result.calculation.inputs.geometry==='focal_plane'){
      const draft=el('button','导出单缝验证草稿');draft.type='button';
      draft.onclick=()=>{
        const calc=result.calculation;
        const body=[result.question,'','程序核算：'+calc.result,'条件：'+calc.condition,
          '知识库版本：'+result.catalog_version,'','在完整 PhotonGuard 工作台粘贴问题和条件，核对并批准计划。'].join('\n');
        const url=URL.createObjectURL(new Blob([body],{type:'text/plain;charset=utf-8'}));
        const file=link('下载草稿',url);file.download='PhotonGuard-单缝验证草稿.txt';file.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      };
      target.append(draft);
    }
  }
  if(current && refs.has('forster-energy-transfer')){
    const demo=el('details',null,'calculation');
    demo.append(el('summary','比较两个距离的 FRET 效率'),fretComparison(question=>{
      byId('question').value=question;
      byId('ask-form').requestSubmit(connection.ai_connected?byId('ask-live'):byId('ask-form').querySelector('[value=local]'));
    }));
    target.append(demo);
  }
  if(current && result.session){
    const follow=el('form',null,'study-followup');
    const field=el('input');field.type='text';field.required=true;field.maxLength=1000;
    const label=el('label','继续这个问题');label.htmlFor='study-followup-input';field.id='study-followup-input';
    const submit=el('button',connection.ai_connected?'请 AI 讲解':'查本地依据');submit.type='submit';
    follow.append(label,field,submit);
    follow.onsubmit=event=>{
      event.preventDefault();byId('question').value=field.value;
      byId('ask-form').requestSubmit(connection.ai_connected?byId('ask-live'):byId('ask-form').querySelector('[value=local]'));
    };
    target.append(follow);
  }
}

function experimentLines(experiment) {
  const {origin,source,detail,comparison,prediction}=experiment;
  const lines=['### 实验依据',`原学习问题：${origin.question}`,`原知识库版本：${origin.catalog_version}`,
    ...origin.references.map(ref=>`${ref.title}（${ref.id} · ${ref.pack} ${ref.version}）`),
    `已保存结果：${experiment.title}（${source.record_id}）`,`结果指纹：${source.result_sha256}`];
  if(origin.prediction)lines.push('公式预测：'+origin.prediction.result);
  if(prediction?.after_mm!=null)lines.push('对照解析预测：'+prediction.after_mm+' mm');
  if(comparison){
    lines.push('对照来源记录：'+comparison.source.record_id+' · '+comparison.source.result_sha256);
    const before=comparison.candidates.find(row=>row.id===comparison.selected_candidate_id);
    if(before?.parameters.aperture==='single_slit'){
      const p=before.parameters;
      lines.push(`来源方案：波长 ${p.wavelength_nm} nm，焦距 ${p.focal_length_mm} mm，缝宽 ${p.slit_width_um} μm`);
    }
    if(before?.goal.value!=null)lines.push(`来源指标 ${before.goal.value} ${before.goal.unit}`);
  }
  if(detail.retained_modes!=null){
    const before=detail.acquisition_comparison?.previous_modes;
    lines.push('有限搜索内保留候选：'+(before==null?'':before+' → ')+detail.retained_modes,
      '已使用波长：'+(detail.observed_wavelengths_nm||[]).join('、')+' nm',
      '数据来源：'+({TEACHING_SYNTHETIC:'教学合成观测',MIXED_OBSERVATIONS:'教学与导入观测',
        USER_SUPPLIED_OBSERVATIONS:'导入观测'}[detail.data_kind]||detail.data_kind));
    if(detail.thickness_bounds_nm)lines.push('有限厚度范围：'+detail.thickness_bounds_nm.join('–')+' nm');
    const added=detail.acquisition_comparison?.added_wavelengths_nm;
    if(added?.length)lines.push('本版增加观测：'+added.join('、')+' nm');
  }
  for(const row of detail.candidates||[]){
    const parameters=row.parameters;
    if(parameters.aperture==='single_slit')lines.push(`${row.id}：波长 ${parameters.wavelength_nm} nm，焦距 ${parameters.focal_length_mm} mm，缝宽 ${parameters.slit_width_um} μm`);
    if(row.goal.value!=null)lines.push(`指标 ${row.goal.value} ${row.goal.unit}（${row.goal_source==='ANALYTIC_GEOMETRY'?'解析几何':'计算记录'}）`);
  }
  lines.push(...experiment.model.map(item=>'模型条件：'+item));
  return lines;
}

function experimentEvidence(experiment) {
  const box=el('details',null,'calculation');box.open=true;
  box.append(el('summary','同一问题 · 已保存的实验依据'));
  for(const line of experimentLines(experiment).slice(1))box.append(el('p',line));
  box.append(link('打开实验结果','/experiments#case='+encodeURIComponent(experiment.source.record_id)));
  return box;
}

function renderHandoff(target,result) {
  const ids=new Set(result.references.map(item=>item.id));
  const query=result.question;
  const slit=[...ids].some(id=>['slit-scale','slit-width-definitions','diffraction-basics'].includes(id))
    && (result.calculation?.inputs?.geometry==='focal_plane' || /焦距|焦平面/.test(query));
  const film=[...ids].some(id=>['film-ambiguity','film-retained-modes','film-reflectance-input'].includes(id));
  if(!slit&&!film)return;
  const box=el('details',null,'calculation');
  box.append(el('summary','带着这个问题去验证'));
  const params=new URLSearchParams({study:result.session.id,turn:result.session.last_turn_id});
  if(film)box.append(link('接续膜厚数据分析','/experiments#inverse-import&'+params));
  if(slit){
    const teaching=el('input');teaching.type='checkbox';
    const label=el('label',null,'consent');
    label.append(teaching,document.createTextNode('缺少实际条件时采用明确标注的教学参数'));
    const status=el('div');status.setAttribute('aria-live','polite');
    const check=el('button','预览单缝验证计划');check.type='button';
    teaching.onchange=()=>status.replaceChildren();
    check.onclick=async()=>{
      check.disabled=true;teaching.disabled=true;status.replaceChildren();
      const body={session_id:result.session.id,turn_id:result.session.last_turn_id,teaching:teaching.checked};
      try{
        const preview=await api('slit',body);
        if(preview.status!=='PREVIEW'){
          status.append(list(preview.questions||[],text=>el('span',text)));
          status.append(link('在单缝表单补齐条件','/experiments#slit-goal&'+params));
          return;
        }
        const p=preview.plan.spec.known_parameters;
        status.append(el('p',`波长 ${p.wavelength_nm} nm · 焦距 ${p.focal_length_mm} mm · 缝高 ${p.slit_height_um} μm`),
          el('p','比较缝宽（μm） '+preview.design.rows.map(row=>row.slit_width_um).join('、')),
          el('p',preview.design.teaching_preset||'使用已核对的实际条件。'));
        const save=el('button','保存待核对计划');save.type='button';
        save.onclick=async()=>{
          save.disabled=true;teaching.disabled=true;
          try{
            const saved=await api('slit',{...body,preview_sha256:preview.preview_sha256});
            location.href='/experiments#case='+encodeURIComponent(saved.case.id);
          }catch(err){status.append(el('p',err.message,'notice'));save.disabled=false;teaching.disabled=false;}
        };
        status.append(save);
      }catch(err){error(status,err);}
      finally{check.disabled=false;teaching.disabled=false;}
    };
    box.append(label,check,status);
  }
  target.append(box);
}

async function ask(event) {
  event.preventDefault();
  const mode=event.submitter?.value || 'local';
  const question=byId('question').value.trim();
  if(!question)return;
  const buttons=[...byId('ask-form').querySelectorAll('button')];buttons.forEach(item=>item.disabled=true);
  byId('answer').replaceChildren(el('p',mode==='live'?'正在请求 AI 讲解…':'正在查阅本地知识…'));
  try {
    const body={question,mode,previous,known,catalog_version:studyVersion||overview.catalog_version};
    if(!sessionId)sessionId=crypto.randomUUID().replaceAll('-','');
    body.session_id=sessionId;body.turn_id=crypto.randomUUID().replaceAll('-','');body.expected_turn_id=lastTurn||null;
    if(resumeTurn)body.resume_turn_id=resumeTurn;
    if(focus)body.focus=focus;
    if(studyPath)body.path=studyPath;
    if(lesson)body.lesson=lesson;
    if(lessonSection)body.section=lessonSection;
    if(selectedText)body.selection=selectedText;
    if(experimentId)body.experiment_id=experimentId;
    if(mode==='live')body.reading=byId('reading').value;
    if(mode==='live')body.approve_data=true;
    const result=await api('ask',body);
    renderAnswer(result);
    lastTurn=result.session.last_turn_id;
    resumeTurn='';
    sessionStorage.setItem(studyKey,JSON.stringify({sessionId,known,experimentId}));
    if(result.status==='FOUND'||result.status==='GENERAL'){
      previous=result.direct.slice(0,8);studyVersion=result.catalog_version;
      studyPath='';focus='';lesson='';lessonSection='';selectedText='';
      studyContext('可追问上一轮的条件、结果和推导。');
      byId('question').value='';
    }
  } catch(err){error(byId('answer'),err);}
  finally{buttons.forEach(item=>item.disabled=false);liveState();}
}

async function restoreStudy() {
  const saved=JSON.parse(sessionStorage.getItem(studyKey) || 'null');
  const routeParams=new URLSearchParams(location.hash.slice(1));
  const linkedStudy=routeParams.get('study'), linkedResult=routeParams.get('experiment');
  if(/^[a-f0-9]{32}$/.test(linkedStudy)){
    const result=await api('sessions/'+linkedStudy);
    sessionId=linkedStudy;lastTurn=result.session.last_turn_id;studyVersion=result.catalog_version;
    previous=result.direct||[];experimentId=/^[a-f0-9]{32}$/.test(linkedResult)?linkedResult:'';
    sessionStorage.setItem(studyKey,JSON.stringify({sessionId,known:[],experimentId}));
    renderAnswer(result);
    studyContext(experimentId?'正在接续原问题。下一轮将结合所选实验结果。':'已展开原对话，可以继续提问。');
    return;
  }
  if(!saved?.sessionId && !saved?.result)return;
  try{
    const result=saved.sessionId ? await api('sessions/'+encodeURIComponent(saved.sessionId)) : saved.result;
    previous=result.direct||[];known=saved.known||[];studyVersion=result.catalog_version;
    sessionId=result.session?.id||'';lastTurn=result.session?.last_turn_id||'';
    experimentId=saved.experimentId||'';
    if(studyVersion===overview.catalog_version)studyContext('可追问上一轮的条件、结果和推导。');
    renderAnswer(result);
  }catch(err){
    sessionStorage.removeItem(studyKey);error(byId('answer'),err);
  }
}

function liveState() {
  byId('ask-live').disabled=!connection?.ai_connected;
}

function showAI() {
  const note=byId('ai-note');
  note.replaceChildren();
  if(connection.ai_connected)note.textContent='DeepSeek 已连接。点击「请 AI 串讲」会发送问题、相关前文与依据，含所选实验摘要（如有）。';
  else if(connection.standalone)note.textContent='可直接查阅本地知识。需要 AI 时连接自己的 DeepSeek 账户。';
  else note.append('本地学习可直接使用。需要 AI 时请先到 ',link('实验工作台','/experiments'),' 连接。');
  liveState();
}

function initStandaloneBinding() {
  byId('workbench-nav').hidden=true;
  byId('standalone-ai').hidden=false;
  byId('study-quota').value=connection.ai.request_limit;
  byId('study-resume-line').hidden=!connection.ai.needs_recovery;
  byId('study-disconnect').disabled=!connection.ai_connected;
  async function bind(disconnect) {
    const connect=byId('study-connect'), stop=byId('study-disconnect'), status=byId('study-binding-status');
    const key=byId('study-key');
    connect.disabled=true;stop.disabled=true;
    status.textContent=disconnect?'正在断开…':'正在验证账户…';
    try {
      const result=await api(disconnect?'ai/disconnect':'ai/connect',disconnect?{}:{
        api_key:key.value,request_limit:Number(byId('study-quota').value),
        approve:byId('study-key-consent').checked,resume:byId('study-resume').checked,
      });
      connection.ai=result;connection.ai_connected=result.live_enabled;
      status.textContent=disconnect?'连接已断开。':`已连接，剩余请求 ${result.requests_remaining} 次。`;
      byId('study-resume-line').hidden=!result.needs_recovery;
      if(!disconnect && result.live_enabled){
        byId('standalone-ai').open=false;
        byId('question').focus();
      }
    }catch(err){status.textContent=err.message;}
    finally{key.value='';connect.disabled=false;stop.disabled=!connection.ai_connected;showAI();}
  }
  byId('study-connect').onclick=()=>bind(false);
  byId('study-disconnect').onclick=()=>bind(true);
}

function showSummary() {
  byId('catalog-summary').textContent=`${overview.published} 篇知识条目 · ${overview.study_topics.length} 个专题 · 支持离线查阅`;
}

function route() {
  const assistant=location.hash==='#assistant'||location.hash.startsWith('#assistant&');
  byId('assistant').hidden=!assistant;byId('library').hidden=assistant;
  byId(assistant?'assistant-nav':'library-nav').setAttribute('aria-current','page');
  byId(assistant?'library-nav':'assistant-nav').removeAttribute('aria-current');
  if(location.hash.startsWith('#article='))showArticle(location.hash.slice(9));
  if(location.hash.startsWith('#topic='))showTopic(location.hash.slice(7));
}

async function start() {
  try {
    [overview,connection]=await Promise.all([api('overview'),api('bootstrap')]);
    showSummary();showNavigation();
    for(const field of ['level','license'])for(const [value,name] of Object.entries(overview[field==='level'?'levels':'licenses'])){
      const option=el('option',name);option.value=value;byId(field).append(option);
    }
    if(connection.standalone)initStandaloneBinding();
    showAI();
    byId('ask-form').onsubmit=ask;byId('reset-study').onclick=reset;
    byId('search-form').onsubmit=event=>{event.preventDefault();page=1;search();};
    for(const field of ['level','license','capability','pack'])byId(field).onchange=()=>{page=1;search();};
    byId('previous').onclick=()=>{page--;search();};byId('next').onclick=()=>{page++;search();};
    await restoreStudy();window.addEventListener('hashchange',()=>{
      route();
      if(new URLSearchParams(location.hash.slice(1)).get('study'))restoreStudy().catch(err=>error(byId('answer'),err));
    });route();search();
  } catch(err){error(byId('article'),err);}
}

start();
