"use strict";

window.photonGuardLearning = ({ api, submitPlan, node, append, card, button, consent, detail, list, plot, openCase, openBounded, aiConnected, selection, viewMode, isCredential }) => {
  const FULL = 'central_zero_to_zero_width_mm';
  const SIDE = 'first_zero_mm';
  const fmt = value => Number.isFinite(value) ? Number(value.toPrecision(7)).toString() : '—';
  const comparedResults = new Map();
  const bounded = window.photonGuardBoundedGoal?.({api,node,append,button,consent,openCase,aiConnected,isCredential});
  const demo = '使用633 nm光和200 mm焦距透镜，希望中央亮区全宽10±0.2 mm。只允许改变缝宽，范围15—35 μm。采用教学模型，其他设置请帮我安排。';

  function field(label, input) {
    input.setAttribute('aria-label', label);
    return append(node('label', null, 'learning-field'), node('span', label), input);
  }

  function table(headers, rows) {
    const header = append(node('tr'), ...headers.map(text => node('th', text)));
    const body = append(node('tbody'), ...rows.map(row => append(node('tr'), ...row.map(text => node('td', text)))));
    return append(node('div', null, 'table-scroll'), append(node('table'), append(node('thead'), header), body));
  }

  function designSummary(host, design, calculated = false) {
    const { restriction, ideal_width_um: ideal, rows, target_width_interval_um: interval } = design;
    const catalog = restriction.kind === 'catalog';
    const lead = (catalog ? '只比较给定目录。' : '解析建议缝宽 ' + fmt(ideal) + ' μm。') +
      (design.analytically_feasible ? '有解析达标方案。' : '当前限制内没有解析达标方案。');
    const closest = rows.find(row => row.slit_width_um === design.closest_width_um);
    append(host, node('p', lead, 'learning-result'),
      node('p', `最接近的候选为 ${fmt(closest.slit_width_um)} μm，对应 ${fmt(closest.predicted_mm)} mm。`, 'field-note'),
      table(['缝宽（μm）', '解析宽度（mm）', '目标'], rows.map(row => [fmt(row.slit_width_um), fmt(row.predicted_mm), row.within_tolerance ? '达标' : '未达标'])),
      node('p', (calculated ? '原方案的解析提议。实际数组与核对见结果。' : '解析预览。批准计划后生成实际 FFT 光强。') +
        ` 公式 ${design.formula_id}，达标缝宽区间 ${fmt(interval.min)} 至 ${interval.max == null ? '无解析上界' : fmt(interval.max)} μm。`, 'field-note'));
    if (design.teaching_preset) host.append(node('p', design.teaching_preset + ' · 理想教学条件。', 'field-note'));
    const labels = { wavelength_nm:'波长', focal_length_mm:'焦距', slit_height_um:'缝高', metric:'指标定义', target_mm:'目标尺寸', tolerance_mm:'容差', min_um:'缝宽下限', max_um:'缝宽上限', catalog_um:'缝宽目录', aperture:'模型', slit_width_um:'候选缝宽', grid_size:'网格', window_mm:'孔径窗口' };
    host.append(detail('参数来源', Object.entries(design.sources).map(([name, source]) => `${labels[name] || name} · ${source.quote}`).join('\n')));
  }

  function slitDesigner(host, study = null) {
    const panel = card('按目标设计单缝实验');
    panel.id = 'slit-goal-designer';
    const text = node('textarea');
    text.rows = 3; text.maxLength = 2400;
    text.placeholder = '写下目标宽度、波长、透镜焦距，以及可用的缝宽范围或目录。';
    const teaching = consent('采用单缝教学预设，未指定的教学条件在计划里列明');
    const share = consent('允许把本轮目标发给 DeepSeek');
    const values = {}, inputs = node('div', null, 'learning-fields');
    const addInput = (key, label, type = 'number') => {
      const input = node('input'); input.type = type;
      if (type === 'number') { input.min = '0'; input.step = 'any'; }
      values[key] = input;
      return field(label, input);
    };
    const metric = node('select');
    for (const [value, label] of [['', '选择宽度定义'], [FULL, '中央全宽（左右第一暗纹间）'], [SIDE, '中心到单侧第一暗纹']]) {
      const option = node('option', label); option.value = value; metric.append(option);
    }
    values.metric = metric;
    append(inputs, field('目标定义', metric), addInput('target_mm', '目标尺寸（mm）'), addInput('tolerance_mm', '允许误差（mm）'),
      addInput('wavelength_nm', '波长（nm）'), addInput('focal_length_mm', '焦距（mm）'), addInput('slit_height_um', '缝高（μm）'),
      addInput('min_um', '缝宽下限（μm）'), addInput('max_um', '缝宽上限（μm）'), addInput('catalog_um', '固定目录（μm，用逗号分隔）', 'text'));
    const advanced = append(node('details'), node('summary', '高级数值设置'), append(node('div', null, 'learning-fields'),
      addInput('grid_size', 'FFT 网格（256 或 512）'), addInput('window_mm', '孔径面窗口（mm）')));
    const preview = node('div', null, 'learning-preview');
    preview.setAttribute('aria-live', 'polite');
    let pending = false, version = 0, excerpts = {};
    const edited = new Set();
    Object.entries(values).forEach(([key, input]) => input.addEventListener('input', () => edited.add(key)));
    const changed = () => { version++; preview.replaceChildren(); };
    const setText = (value, preset = false) => {
      text.value = value;
      teaching.input.checked = preset;
      excerpts = {};
      edited.clear();
      Object.values(values).forEach(input => { input.value = ''; });
      changed();
      text.focus();
    };
    const fields = () => {
      const result = {};
      for (const [key, input] of Object.entries(values)) {
        if (!input.value.trim()) continue;
        if (key === 'metric') result[key] = input.value;
        else if (key === 'catalog_um') {
          result[key] = input.value.trim().split(/\s*[,，、]\s*/).map(value => Number(value));
          if (result[key].some(value => !Number.isFinite(value) || value <= 0)) throw new Error('请用逗号分隔正数缝宽。');
        } else result[key] = Number(input.value);
      }
      return result;
    };
    async function review(mode, readText) {
      if (pending) return;
      pending = true;
      const stamp = version;
      preview.replaceChildren(node('p', mode === 'live' ? '正在核对 AI 提取的原文条件…' : '正在核对条件…', 'field-note'));
      try {
        let source = '核对后的目标表单';
        if (readText) {
          const intake = await api('/learning/slit/inspect', {text:text.value, mode, ...(mode === 'live' ? {approve_data:share.input.checked} : {})});
          if (stamp !== version || !panel.isConnected) return;
          teaching.input.checked ||= intake.teaching;
          excerpts = intake.quotes || {};
          for (const [key, value] of Object.entries(intake.values)) {
            if (values[key] && !edited.has(key)) values[key].value = Array.isArray(value) ? value.join(', ') : value;
          }
          source = ({LIVE_AI_REVIEWED_QUOTES:'DeepSeek 提取已与原文核对', LOCAL_INTAKE_AFTER_AI_REJECTION:'AI 提取未通过来源核对，已使用本地条件', LOCAL_INTAKE:'本地整理'})[intake.source] || '核对后的条件';
          if (intake.source === 'LIVE_AI_REVIEWED_QUOTES' && Object.keys(intake.ai_rejected_fields || {}).length) {
            source = '已核对 DeepSeek 能绑定原文的条件，其余沿用原文中已确认的资料。';
          }
        }
        const formValues = fields();
        const body = {text:text.value, teaching:teaching.input.checked, values:formValues, excerpts,
          confirmed_fields:[...edited].filter(key => Object.hasOwn(formValues, key))};
        const result = await api('/learning/slit/preview', body);
        if (stamp !== version || !panel.isConnected) return;
        preview.replaceChildren(node('p', source, 'field-note'));
        if (result.status !== 'PREVIEW') {
          preview.append(list(result.questions));
          if (result.status === 'NEEDS_INFORMATION') fieldsGroup.open = true;
          return;
        }
        designSummary(preview, result.design);
        const create = button('创建计划并核对', async () => {
          if (pending || stamp !== version) return;
          pending = true; create.disabled = true;
          try {
            const saved = await submitPlan('/learning/slit/plan', {...body, preview_sha256:result.preview_sha256,
              ...(study ? {study} : {})});
            if (saved.case && panel.isConnected) openCase(saved.case, '计划已保存。核对后可开始计算。');
          } catch (error) { preview.append(node('p', error.message, 'message error')); }
          finally { pending = false; create.disabled = false; }
        }, 'primary');
        preview.append(create);
      } catch (error) { if (stamp === version) preview.replaceChildren(node('p', error.message, 'message error')); }
      finally { pending = false; }
    }
    const live = button('AI 整理并预览', () => review('live', true), 'small', true);
    share.input.addEventListener('change', () => { live.disabled = !aiConnected() || !share.input.checked; });
    panel.addEventListener('input', event => { if (event.target !== share.input) changed(); });
    text.addEventListener('input', () => { excerpts = {}; edited.clear(); Object.values(values).forEach(input => { input.value = ''; }); });
    const fieldsGroup = append(node('details', null, 'learning-fields-group'), node('summary', '查看或补充已知条件'),
      inputs, advanced, button('按表单重新预览', () => review('local', false), 'small'));
    append(panel, field('单缝目标', text), teaching.wrap,
      append(node('div', null, 'actions'),
        button('填入教学任务', () => setText(demo, true), 'small'),
        button('本地整理并预览', () => review('local', true), 'primary'), live),
      aiConnected() ? share.wrap : node('p', '可先用本地整理。连接 AI 后可选 AI 提取。', 'field-note'),
      fieldsGroup, preview);
    host.append(panel);
    return (value, preset = !value) => setText(value || demo, preset);
  }

  function knowledgeSource(source) {
    const link = node('a', source.title);
    link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    return link;
  }

  function knowledgeCard(info) {
    const block = append(node('details'), node('summary', info.title), node('p', info.definition), node('p', info.detailed_text));
    if (info.formula) append(block, node('p', info.formula, 'learning-formula'), node('p', info.variables, 'field-note'));
    append(block, node('p', info.assumptions.join(' · '), 'field-note'));
    if (info.common_mistakes?.length) append(block, node('h4','容易混淆的地方'), list(info.common_mistakes));
    if (info.verification) append(block, node('h4','怎样核对'), node('p', info.verification));
    const links = node('ul', null, 'concise');
    for (const source of info.sources) links.append(append(node('li'), knowledgeSource(source)));
    block.append(links);
    return block;
  }

  function teachingDiagram(answer) {
    if (!/用图|这张图|图上/.test(answer.question)) return null;
    if (['gaussian_rayleigh', 'gaussian-waist'].includes(answer.conclusion?.id)) {
      const x = Array.from({length:41}, (_, i) => (i - 20) / 10);
      const marks = [{x:0,label:'束腰（半径最小）'}];
      if (answer.conclusion.id === 'gaussian_rayleigh') marks.push({x:1,label:'距束腰一个瑞利长度，半径为 √2 倍'});
      return plot({x, series:[{label:'归一化光斑半径', y:x.map(z => Math.hypot(1, z))}],
        x_label:'距束腰的位置 / 瑞利长度', y_label:'光斑半径 / 束腰半径', analytic_reference:true},
        null, marks);
    }
    if (answer.result?.domain === 'diffraction') {
      return button('查看当前曲线与暗纹', () => document.getElementById('learning-canvas')?.scrollIntoView({block:'start'}), 'small');
    }
    return null;
  }

  function tutor(host, saved = null, workspace = host, {versions = [], openVersion, figure, clearFigure} = {}) {
    const panel = card();
    panel.id = 'optics-tutor';
    const q = node('textarea'); q.rows = 2; q.maxLength = 1600;
    const examples = {diffraction:'为什么缝越宽，中央亮区越窄？', gaussian:'屏幕位置和新束腰有什么区别？',
      film:'为什么同一个反射率会有多个膜厚？'};
    q.placeholder = '例如：' + (saved ? examples[saved.domain] || '这版结果该怎么看？' : '什么是瑞利长度？');
    const figureText = node('span');
    const figureFocus = append(node('div',null,'actions learning-figure-focus'), figureText,
      button('清除图形选择', () => clearFigure?.(), 'small'));
    figureText.setAttribute('aria-live','polite');
    function updateFigure() {
      const chosen = figure?.();
      figureText.textContent = chosen ? '选中的图形 · ' + chosen.label : '';
      figureFocus.hidden = !chosen;
    }
    const output = node('div', null, 'learning-preview'); output.setAttribute('aria-live', 'polite');
    const historyList = node('ol', null, 'learning-turns');
    const historyTitle = node('summary', '本次学习对话');
    const transcript = append(node('details'), historyTitle, historyList);
    transcript.hidden = true;
    const taskId = saved?.experiment_record?.result_sha256 && saved?.family_id;
    const scope = taskId ? 'task:' + taskId : saved ? saved.id + ':' + (saved.experiment_record?.result_sha256 || 'saved') : 'theory';
    const storageKey = 'photonguard-learning-session:' + scope;
    const pendingKey = storageKey + ':pending';
    const recovery = node('div', null, 'learning-preview');
    const compare = node('select');
    const compareCandidate = node('select');
    const compareCandidateField = field('参照候选', compareCandidate);
    compareCandidateField.hidden = true;
    const options = new Map();
    if (taskId) {
      for (const record of versions) {
        if (record.family_id === taskId && record.id !== saved.id && record.status === 'COMPLETED' && record.domain === saved.domain &&
            record.kind !== 'observation' && (record.kind === 'inverse') === (saved.kind === 'inverse')) {
          options.set(record.id, record.title);
        }
      }
      for (const version of saved.experiment_record.available_versions || []) {
        if (version.id !== saved.id && !options.has(version.id)) options.set(version.id, '保存版本');
      }
    }
    const defaultOption = node('option','仅讨论当前结果'); defaultOption.value = ''; compare.append(defaultOption);
    for (const [id, title] of options) { const option = node('option', title + ' · ' + id.slice(0, 8)); option.value = id; compare.append(option); }
    const id = () => crypto.randomUUID().replaceAll('-', '');
    let sessionId, session = null, depth = 'short', pending = false, pendingBody = null;
    try { sessionId = sessionStorage.getItem(storageKey); } catch (_) {}
    if (!/^[a-f0-9]{32}$/.test(sessionId || '')) sessionId = id();
    const depthControls = node('div', null, 'actions learning-depth');
    depthControls.setAttribute('role', 'group'); depthControls.setAttribute('aria-label', '讲解方式');
    const prompts = {short:'再简单一点。', formula:'我想看推导。', result:'用刚才那组参数解释。'};
    const depthButtons = {};
    function setDepth(value) {
      depth = value;
      for (const [key, control] of Object.entries(depthButtons)) control.setAttribute('aria-pressed', String(key === value));
    }
    function showSession(value) {
      session = value;
      historyList.replaceChildren();
      transcript.hidden = !session?.turn_count;
      if (!session?.turn_count) return;
      historyTitle.textContent = '本次学习对话（' + session.turn_count + ' 轮）';
      for (const turn of session.turns) {
        const row = append(node('li'), node('p', turn.question, 'learning-question'));
        if (turn.conclusion) row.append(node('p', turn.conclusion.text));
        row.append(node('p', turn.answer, 'field-note'));
        if (turn.candidate_id) row.append(node('small', '候选 ' + turn.candidate_id, 'muted'));
        if (turn.figure) row.append(node('p', '图形 · ' + turn.figure.label, 'field-note'));
        if (turn.source) row.append(node('p','结果 ' + turn.source.record_id.slice(0, 8) +
          (turn.comparison_source ? ' · 对照 ' + turn.comparison_source.record_id.slice(0, 8) : ''),'field-note'));
        historyList.append(row);
      }
      try { sessionStorage.setItem(storageKey, sessionId); } catch (_) {}
    }
    function savedRequest() {
      try {
        const item = JSON.parse(sessionStorage.getItem(pendingKey) || 'null');
        return item ? (item.session_id === sessionId ? item : null) : pendingBody;
      } catch (_) { return pendingBody; }
    }
    function rememberRequest(body) {
      pendingBody = body;
      try { sessionStorage.setItem(pendingKey, JSON.stringify(body)); } catch (_) {}
    }
    function clearRequest(body) {
      if (!body || savedRequest()?.turn_id !== body.turn_id) return;
      pendingBody = null;
      try { sessionStorage.removeItem(pendingKey); } catch (_) {}
    }
    function showRecovery() {
      const body = savedRequest();
      recovery.replaceChildren();
      if (body && !pending) append(recovery, node('p','上一问的状态待确认。','field-note'),
        button('读取上一问的结果',()=>ask(body.mode,'',body),'small'),button('刷新对话记录',restore,'small'));
    }
    async function restore() {
      const requested = sessionId;
      try {
        const found = await api('/learning/session/' + requested);
        if (requested !== sessionId || !panel.isConnected) return;
        showSession(found);
        setDepth(found.turns.at(-1)?.depth || 'short');
        const waiting = savedRequest();
        if (waiting && found.last_turn_id !== waiting.expected_turn_id) { clearRequest(waiting); transcript.open = true; }
      } catch (error) {
        if (error.httpStatus !== 404 && panel.isConnected) output.replaceChildren(node('p', error.message, 'message error'));
      } finally { if (panel.isConnected) showRecovery(); }
    }
    async function ask(mode, preset = '', replay = null) {
      const question = (replay?.question || preset || q.value).trim();
      if (pending || !question) return;
      const wantsAI = mode === 'live';
      if (wantsAI && !aiConnected()) {
        output.replaceChildren(node('p','请先连接 DeepSeek，再向 AI 提问。','message error'));
        return;
      }
      if (isCredential(question)) { output.replaceChildren(node('p','请输入不含密钥的光学问题。','message error')); return; }
      const candidateId = saved ? selection(saved) : null;
      const binding = saved ? {record_id:saved.id, ...(candidateId ? {candidate_id:candidateId} : {})} : {};
      const comparisonId = compare.value;
      const comparisonCandidate = compareCandidate.value || (comparisonId && selection({id:comparisonId}));
      if (saved?.experiment_record?.result_sha256) binding.result_sha256 = saved.experiment_record.result_sha256;
      if (taskId) binding.learning_task_id = taskId;
      if (saved?.domain === 'diffraction') binding.view = viewMode?.(saved) || 'physical';
      const chosen = figure?.();
      if (chosen) binding.figure_id = chosen.id;
      pending = true;
      await ready;
      if (!replay && savedRequest()) { pending = false; showRecovery(); return; }
      output.replaceChildren(node('p', wantsAI ? '正在请求 AI 讲解…' : '正在查阅本地知识…', 'field-note'));
      let body = replay;
      try {
        if (!panel.isConnected) return;
        if (!replay && comparisonId) {
          const reference = await api('/cases/' + encodeURIComponent(comparisonId));
          if (!panel.isConnected) return;
          const resultHash = reference.experiment_record?.result_sha256;
          if (!resultHash) throw new Error('所选版本还没有可用于对照的结果。');
          const candidate = comparisonCandidate;
          binding.compare_to = {record_id:reference.id,result_sha256:resultHash,...(candidate ? {candidate_id:candidate} : {})};
        }
        try { sessionStorage.setItem(storageKey, sessionId); } catch (_) {}
        body ||= {question, mode, ...binding, depth,
          session_id:sessionId, turn_id:id(), expected_turn_id:session?.last_turn_id || null,
          ...(wantsAI ? {approve_data:true} : {})};
        rememberRequest(body);
        const answer = await api('/learning/ask', body);
        clearRequest(body);
        if (!panel.isConnected) return;
        showSession(answer.session); setDepth(answer.depth);
        if (!preset && q.value.trim() === question) q.value = '';
        const label = answer.mode === 'LIVE_AI_GENERAL' ? 'DeepSeek 通识讲解'
          : answer.mode === 'INJECTED_AI_GENERAL' ? '测试模型通识讲解'
          : answer.mode === 'LOCAL_REFERENCE_AFTER_AI_FAILURE' ? 'AI 暂未完成讲解'
          : answer.mode === 'LIVE_AI' ? (answer.explanation_source === 'REGISTERED' ? 'DeepSeek 选择讲解方式' : 'DeepSeek 讲解')
          : answer.mode === 'INJECTED_AI' ? '测试模型解读' : answer.mode === 'SAVED_EXPLANATION' ? '回看已保存的解释'
          : answer.retrieval?.basis === 'CLARIFY' ? '需要明确问题' : '本地知识';
        output.replaceChildren(node('p', label, 'eyebrow'));
        const explanation = window.photonStudyText(node('p'), answer.intro);
        explanation.dataset.tutorAnswer = '';
        const aiAnswer = ['LIVE_AI','LIVE_AI_GENERAL','INJECTED_AI','INJECTED_AI_GENERAL'].includes(answer.mode)
          || answer.mode === 'SAVED_EXPLANATION' && answer.explanation_source?.startsWith('LLM_');
        if (aiAnswer) output.append(explanation);
        const evidenceStart = output.childElementCount;
        if (answer.figure) output.append(node('p','正在解释 · ' + answer.figure.label,'field-note'));
        if (answer.conclusion) append(output, node('p', answer.conclusion_source === 'PROGRAM_RESULT' ? '程序核算结果' : '本地知识摘要', 'field-note'),
          node('p', answer.conclusion.text, 'learning-result'));
        if (!aiAnswer) output.append(explanation);
        if (answer.choices?.length) {
          output.append(list(answer.choices.map(entry=>entry.title+' · '+entry.definition)));
          for (const entry of answer.choices) {
            const article = node('a', '阅读' + entry.title, 'button small');
            article.href = '/knowledge#article=' + encodeURIComponent(entry.id);
            output.append(article);
          }
        }
        const primary = answer.cards.find(card => card.id === answer.topic?.id);
        if (primary?.sources?.length) output.append(append(node('p', '知识依据 · ', 'field-note'), knowledgeSource(primary.sources[0])));
        append(output, teachingDiagram(answer));
        if (answer.notice) output.append(node('p', answer.notice, 'field-note'));
        if (answer.source) {
          const selected = answer.result?.selected_candidate_id;
          append(output, node('p', '依据记录 ' + answer.source.record_id.slice(0, 8) + ' · 结果 ' + answer.source.result_sha256.slice(0, 12) + (selected ? ' · 所选 ' + selected : ''), 'field-note'), list(answer.focused_facts || answer.grounded_facts));
          const slit = answer.result?.candidates?.some(candidate=>candidate.id === selected && candidate.parameters?.aperture === 'single_slit');
          if (openBounded && slit && saved?.id === answer.source.record_id && /保持|保住|恢复|维持/.test(question) && /全宽|亮区|图样宽/.test(question)) {
            output.append(button('用这句话打开保宽规划',()=>openBounded(saved.id,selected,question),'small'));
          }
          const side = {slit_dark_fringe_left:'左侧', slit_dark_fringe_right:'右侧'}[answer.highlight];
          const highlight = Boolean(side);
          if (answer.source.record_id !== saved?.id && openVersion) {
            output.append(button('打开这条答复依据的结果', () => openVersion(answer.source.record_id), 'small'));
          } else if (selected && (highlight || answer.revisited_turn_id && /上一张图/.test(question))) {
            output.append(button(highlight ? '在曲线上看' + side + '第一暗纹' : '查看该轮候选曲线', () => {
              const plotChoice = workspace.querySelector('[aria-label="选择要查看的候选曲线"]');
              if (!plotChoice) return;
              if (![...plotChoice.options].some(option => option.value === selected)) return;
              const display = workspace.querySelector('[aria-label="光强显示方式"]');
              if (display && [...display.options].some(option => option.value === answer.view)) display.value = answer.view;
              plotChoice.value = selected;
              plotChoice.dispatchEvent(new Event('change'));
              const marker = highlight && workspace.querySelector('#learning-canvas [data-marker-id="' + answer.highlight + '"]');
              if (marker) { marker.scrollIntoView({block:'center'}); marker.focus({preventScroll:true}); }
              else plotChoice.scrollIntoView({block:'center'});
            }, 'small'));
          }
        }
        if (answer.comparison) {
          const pair = answer.comparison;
          const lines = pair.entries.map(entry => `${entry.label} · ${fmt(entry.reference_value)} → ${fmt(entry.current_value)} ${entry.unit}`);
          const sourceLabel = (binding, candidate) => binding.record_id.slice(0, 8) + (candidate ? ' / ' + candidate : '');
          append(output,node('p','保存结果对照 · ' + sourceLabel(pair.reference, pair.reference_candidate_id)
            + ' → ' + sourceLabel(pair.current, pair.current_candidate_id),'field-note'),list(lines),
            pair.causes ? changeCauses(pair.causes) : null,
            openVersion ? button('打开对照结果',()=>openVersion(pair.reference.record_id),'small') : null);
        }
        if (answer.prediction) {
          const p = answer.prediction;
          const changes = (p.changes || [p.change]).filter(item => !item.state || item.state === 'CHANGE');
          if (changes.length) output.append(node('p', '解析预览 · ' + changes.map(c => `${c.label} ${fmt(c.before)} → ${fmt(c.after)} ${c.unit}`).join('，') + '。', 'learning-result'));
          if (p.verification) output.append(node('p', p.verification.strategy === 'HOLD_WAVELENGTH_FOCAL_PRODUCT'
            ? '对照方法 · 保持缝宽与缝高，让焦距补偿波长变化。'
            : '对照方法 · 只改变波长，焦距与开口尺寸保持。', 'field-note'));
          if (p.issue) output.append(node('p', p.issue, 'field-note'));
          if (answer.suggestion === 'REVIEW_PREVIEW') output.append(node('p', '可核对改动，再准备实际验证。', 'field-note'));
          if (p.can_prepare && !saved?.bounded_goal) output.append(button(answer.verification_round_required ? '另开一轮验证' : '准备实际验证', async () => {
            if (pending) return;
            pending = true;
            try {
              const out = await api('/learning/followup', {question, ...p.source, candidate_id:p.candidate_id, prediction_sha256:p.sha256,
                confirm_change:true, ...(answer.verification_round_required ? {new_round:true} : {})});
              if (out.case && panel.isConnected) openCase(out.case, '已另存对照计划。核对改动后批准计算。');
            } catch (error) { output.append(node('p', error.message, 'message error')); }
            finally { pending = false; }
          }, 'small'));
        } else if (answer.next_step === 'REVIEW_SAVED_ACQUISITION') {
          output.append(button('查看本版补测方案', () => document.getElementById('film-acquisition-plans')?.scrollIntoView({block:'start'}), 'small'));
        }
        if (answer.cards.length) {
          const formulas = append(node('details'), node('summary', '展开公式与出处'), ...answer.cards.map(knowledgeCard));
          const first = answer.cards[0];
          const check = append(node('details'), node('summary', '可选自测'), node('p', `用自己的话解释「${first.title}」。`),
            append(node('details'), node('summary', '查看参考'), node('p', first.intuitive_text)));
          append(output, formulas, check);
        }
        if (answer.grounded_facts.length > (answer.focused_facts || []).length) {
          output.append(append(node('details'), node('summary', '展开完整结果依据'), list(answer.grounded_facts)));
        }
        if (aiAnswer && answer.cards.length) {
          const evidence = node('div'); evidence.dataset.tutorEvidence = '';
          evidence.append(...Array.from(output.children).slice(evidenceStart));
          output.append(evidence);
        }
        if (answer.session?.last_turn_id) output.append(button('展开到学习页',async()=>{
          try{
            const study=await api('/knowledge/expand-tutor',{session_id:answer.session.id,turn_id:answer.session.last_turn_id});
            location.href='/knowledge#assistant&study='+encodeURIComponent(study.session.id);
          }catch(error){output.append(node('p',error.message,'message error'));}
        },'small'));
      } catch (error) {
        if (!error.uncertain && error.httpStatus !== 409) clearRequest(body);
        output.replaceChildren(node('p', error.message, 'message error'));
        if (error.uncertain || error.httpStatus === 409) output.append(button('读取最新对话', restore, 'small'));
      }
      finally { pending = false; if (panel.isConnected) showRecovery(); }
    }
    const live = button('向 AI 提问', () => ask('live'), 'small', !aiConnected());
    compare.addEventListener('change', async () => {
      const versionId = compare.value;
      compareCandidate.replaceChildren();
      compareCandidateField.hidden = true;
      if (!versionId) return;
      try {
        const reference = await api('/cases/' + encodeURIComponent(versionId));
        if (!panel.isConnected || compare.value !== versionId) return;
        const candidates = reference.result?.candidates || [];
        if (candidates.length < 2) return;
        for (const row of candidates) {
          const width = row.parameters?.slit_width_um;
          const label = Number.isFinite(width) ? `${fmt(width)} μm · ${row.id}` : row.id;
          const option = node('option', label);
          option.value = row.id;
          compareCandidate.append(option);
        }
        compareCandidate.value = selection(reference) || reference.result.winner_id || candidates[0].id;
        compareCandidateField.hidden = false;
      } catch (error) {
        if (panel.isConnected && compare.value === versionId)
          output.replaceChildren(node('p', error.message, 'message error'));
      }
    });
    for (const [key, title] of [['short','直观看懂'], ['formula','公式推导'], ['result','结合本次结果']]) {
      if (key === 'result' && !saved) continue;
      const control = button(title, () => {
        if (pending) return;
        setDepth(key);
        if (session?.turn_count && !q.value.trim()) ask('local', prompts[key]);
      }, 'small');
      depthButtons[key] = control; depthControls.append(control);
    }
    setDepth('short');
    const reset = button('开始新对话', () => {
      if (pending || savedRequest()) return;
      sessionId = id(); showSession(null); setDepth('short');
      try { sessionStorage.removeItem(storageKey); } catch (_) {}
      output.replaceChildren(); q.focus();
    }, 'small');
    append(panel, depthControls, options.size ? field('参照版本', compare) : null, compareCandidateField, figureFocus,
      field(saved ? '关于这版结果的问题' : '光学概念问题', q),
      append(node('div', null, 'actions'), button('查本地知识', () => ask('local'), 'small'), live),
      node('p', aiConnected() ? (saved ? '向 DeepSeek 发送问题、相关前文和所选结果摘要。'
        : '向 DeepSeek 发送问题和相关前文。') : '实时问答需要连接 AI。', 'field-note'), output, recovery, transcript, reset);
    const library = node('a','浏览光学知识库');
    library.href = '/knowledge';
    panel.append(library);
    host.append(panel);
    updateFigure();
    const ready = restore();
    return updateFigure;
  }

  function exploration(host, record, candidate, updateMarkers) {
    if (candidate.all_checks_passed !== true) return;
    bounded?.controls(host,record,candidate);
    if (record.bounded_goal) return;
    if (candidate.parameters?.aperture !== 'single_slit') {
      if (record.domain === 'gaussian' || candidate.parameters?.aperture === 'double_slit') comparisonControls(host, record, candidate);
      return;
    }
    const original = candidate.parameters;
    const inputs = new Map();
    const status = node('p', null, 'learning-result'); status.setAttribute('aria-live', 'polite');
    const controls = node('div', null, 'learning-control-grid');
    const panel = append(node('details', null, 'learning-controls'), node('summary', '拖动参数，预览暗纹位置'), controls);
    const fields = [
      ['wavelength_nm', '波长', 'nm', 200, 2000], ['focal_length_mm', '焦距', 'mm', Math.max(1, original.focal_length_mm / 3), Math.min(10000, original.focal_length_mm * 3)],
      ['slit_width_um', '缝宽', 'μm', record.spec?.candidate_bounds?.slit_width_um?.min, record.spec?.candidate_bounds?.slit_width_um?.max],
      ['slit_height_um', '缝高', 'μm', Math.max(1, original.slit_height_um / 2), Math.min(5000, original.slit_height_um * 2)],
    ];
    const errors = node('div');
    let pending = false;
    function typedValues() {
      const data = {};
      for (const [key, input] of inputs) {
        if (!input.value.trim() || !input.checkValidity()) return null;
        const value = Number(input.value);
        if (!Number.isFinite(value) || value <= 0) return null;
        if (value !== original[key]) data[key] = value;
      }
      return data;
    }
    const prepare = button('准备实际验证', async () => {
      const changes = typedValues();
      if (pending || !changes || !Object.keys(changes).length) return;
      pending = true; prepare.disabled = true; errors.replaceChildren();
      try {
        await prepareControls(record, candidate, changes,
          () => panel.isConnected && JSON.stringify(typedValues()) === JSON.stringify(changes));
      } catch (error) { errors.replaceChildren(node('p', error.message, 'message error')); }
      finally { pending = false; refresh(); }
    }, 'primary', true);
    function refresh() {
      const changes = typedValues();
      prepare.disabled = pending || !changes || !Object.keys(changes).length;
      if (!changes) {
        status.textContent = '请填写各控件范围内的数值。';
        updateMarkers(null);
        return;
      }
      const next = {...original, ...changes};
      const horizontal = 2 * next.wavelength_nm * next.focal_length_mm / (1000 * next.slit_width_um);
      const vertical = 2 * next.wavelength_nm * next.focal_length_mm / (1000 * next.slit_height_um);
      status.textContent = `解析预览 · 横向全宽 ${fmt(horizontal)} mm · 纵向全宽 ${fmt(vertical)} mm`;
      updateMarkers(Object.keys(changes).length ? horizontal / 2 : null);
    }
    for (const [key, name, unit, lo, hi] of fields) {
      const label = '预览' + name + '（' + unit + '）';
      if (key === 'slit_width_um' && (lo == null || hi == null)) {
        const picker = node('select');
        for (const value of record.spec?.allowed_variables?.slit_width_um || [original[key]]) {
          const option = node('option', fmt(value)); option.value = value; picker.append(option);
        }
        picker.value = original[key]; picker.addEventListener('change', refresh);
        inputs.set(key, picker); controls.append(field(label, picker));
        continue;
      }
      const slider = node('input'), numeric = node('input');
      slider.type = 'range'; numeric.type = 'number';
      for (const control of [slider, numeric]) {
        control.min = lo; control.max = hi; control.step = 'any'; control.value = original[key];
      }
      slider.setAttribute('aria-label', '拖动' + name);
      slider.addEventListener('input', () => { numeric.value = fmt(Number(slider.value)); refresh(); });
      numeric.addEventListener('input', () => { if (numeric.value) slider.value = numeric.value; refresh(); });
      inputs.set(key, numeric);
      controls.append(append(node('div', null, 'learning-control'), field(label, numeric), slider));
    }
    append(panel, status, node('p', '虚线预览来自理想焦平面公式，实际曲线和二维光强保留在当前版本。', 'field-note'),
      append(node('div', null, 'actions'), prepare), errors);
    host.append(panel); refresh();
  }

  async function prepareControls(record, candidate, changes, current) {
    const binding = {record_id:record.id, result_sha256:record.experiment_record?.result_sha256, candidate_id:candidate.id};
    return prepareBoundControls(binding, changes, current);
  }

  async function prepareBoundControls(binding, changes, current, newRound = false) {
    const preview = await api('/learning/followup/preview', {...binding, changes});
    if (!current()) return;
    if (!preview.prediction?.can_prepare) throw new Error(preview.prediction?.issue || '请核对本轮参数。');
    const saved = await api('/learning/followup', {...binding, changes, prediction_sha256:preview.prediction.sha256,
      confirm_change:true, ...(newRound ? {new_round:true} : {})});
    if (saved.case && current()) openCase(saved.case, '已另存对照计划。核对改动后批准计算。');
  }

  function comparisonControls(host, record, candidate) {
    const gaussian = record.domain === 'gaussian';
    const controls = gaussian
      ? [['screen_distance_mm', '屏幕位置', 'mm']]
      : [['slit_width_um', '缝宽', 'μm'], ['slit_separation_um', '双缝中心距', 'μm'], ['relative_phase_rad', '相对相位', 'rad']];
    const chooser = node('select');
    for (const [key, name] of controls) {
      const option = node('option', name); option.value = key; chooser.append(option);
    }
    const panel = append(node('details', null, 'learning-controls'), node('summary', gaussian ? '换一个屏幕位置' : '只改一个量，观察双缝图样'));
    panel.id = gaussian ? 'screen-comparison' : 'double-slit-comparison';
    const fieldHost = node('div'), error = node('div');
    let pending = false, input = null;
    const prepare = button('准备实际验证', async () => {
      if (pending || !input?.value || !input.checkValidity()) return;
      const key = chooser.value, value = Number(input.value);
      pending = true; prepare.disabled = true; error.replaceChildren();
      try {
        await prepareControls(record, candidate, {[key]:value},
          () => panel.isConnected && chooser.value === key && Number(input.value) === value);
      } catch (err) { error.replaceChildren(node('p', err.message, 'message error')); }
      finally { pending = false; update(); }
    }, 'primary', true);
    const update = () => { prepare.disabled = pending || !input.value || !input.checkValidity() || Number(input.value) === candidate.parameters[chooser.value]; };
    function showField() {
      const [key, name, unit] = controls.find(row => row[0] === chooser.value);
      const catalog = record.spec?.allowed_variables?.[key], bounds = record.spec?.candidate_bounds?.[key];
      input = node(catalog && !bounds ? 'select' : 'input');
      if (catalog && !bounds) {
        for (const value of catalog) {
          const option = node('option', fmt(value)); option.value = value; input.append(option);
        }
      } else {
        input.type = 'number'; input.step = 'any';
        if (gaussian) { input.min = 0; input.max = candidate.parameters.z_max_mm; }
        else if (bounds) { input.min = bounds.min; input.max = bounds.max; }
      }
      input.value = candidate.parameters[key]; input.addEventListener('input', update); input.addEventListener('change', update);
      fieldHost.replaceChildren(field('新的' + name + '（' + unit + '）', input)); update();
    }
    chooser.addEventListener('change', showField);
    append(panel, gaussian ? node('p', '保留当前透镜，比较新屏幕处的光斑半径与同一个束腰。', 'field-note')
      : node('p', '缝宽影响包络，中心距影响条纹周期，相对相位影响中心亮暗。', 'field-note'),
      gaussian ? null : field('选择要改变的量', chooser), fieldHost, append(node('div', null, 'actions'), prepare), error);
    host.append(panel); showField();
  }

  function summaryLink(record) {
    const link = node('a', '下载学习记录', 'button small');
    link.href = '/api/experiments/cases/' + encodeURIComponent(record.id) + '/learning-record';
    link.download = 'PhotonGuard-' + record.id.slice(0, 8) + '-学习记录.md';
    return link;
  }

  function changeCauses(causes, source = null) {
    const direction = {INCREASE:'增大',DECREASE:'减小',UNCHANGED:'保持'};
    const net = causes.opposing
      ? (causes.direction === 'UNCHANGED' ? '各项作用相互抵消。' : '各项作用部分抵消。')
      : '';
    const dominant = causes.effects.find(row=>row.field===causes.dominant_field);
    const lead = causes.direction === 'UNCHANGED' ? `保持在 ${fmt(causes.initial_mm)} mm`
      : `由 ${fmt(causes.initial_mm)} mm ${direction[causes.direction]}为 ${fmt(causes.combined_mm)} mm`;
    const box = append(node('section'),node('h4','为什么这样变'),
      node('p', causes.label + lead + '。' + net + (dominant ? `${dominant.label}的作用较大。` : '')));
    if (typeof causes.original_target?.met === 'boolean') {
      const goal = causes.original_target;
      const threshold = goal.operator === 'near' ? `${fmt(goal.target)} ± ${fmt(goal.tolerance)} mm` : `${goal.operator} ${fmt(goal.target)} mm`;
      box.append(node('p', `按来源目标 ${threshold} 核对 · 本版${goal.met ? '达标' : '未达标'}。`, 'field-note'));
    }
    const details = append(node('details'),node('summary','查看各项作用与解析预览'),
      table(['每次只改一项','条件变化',`解析预测 · ${causes.label}`,'倍率'],
        causes.effects.map(row=>[row.label,`${fmt(row.before)} → ${fmt(row.after)} ${row.unit}`,
          `${fmt(row.isolated_mm)} mm · ${direction[row.direction]}`,fmt(row.ratio)])),
      node('p',`先固定其他条件，再将各项倍率相乘。合并倍率 ${fmt(causes.ratio)}。预览未运行 FFT。`,'field-note'));
    if (source) {
      const controls = node('div',null,'actions');
      const status = node('p',null,'field-note');
      status.setAttribute('aria-live','polite');
      for (const row of causes.effects) {
        if (row.issue) details.append(node('p', `只改${row.label} · ${row.issue}`, 'field-note'));
        if (!row.can_prepare) continue;
        const label = (causes.new_round_required ? '另开一轮，只改' : '准备只改') + row.label + '的对照';
        const prepare = button(label, async () => {
          prepare.disabled = true;
          try { await prepareBoundControls(source, {[row.field]:row.after}, () => box.isConnected, causes.new_round_required); }
          catch(error) { if (box.isConnected) status.textContent = error.message; }
          finally { prepare.disabled = false; }
        },'small');
        controls.append(prepare);
      }
      if (causes.new_round_required)
        details.append(node('p','来源的两阶段探索已用完。另开一轮会保留旧结果和额度记录，计算仍需批准。','field-note'));
      append(details, controls, status);
    }
    box.append(details);
    return box;
  }

  function comparison(host, record) {
    const prediction = record.slit_followup || record.learning_comparison;
    if (!prediction) return;
    const box = card('本轮改变与对照');
    box.id = 'learning-comparison';
    const changes = (prediction.changes || [prediction.change]).filter(item => !item.state || item.state === 'CHANGE');
    append(box, node('p', changes.map(change => `${change.label} ${fmt(change.before)} → ${fmt(change.after)} ${change.unit}`).join('，') + '。其余条件沿用来源候选。'),
      record.slit_followup ? node('p', `解析预览 · ${prediction.metric_label} ${fmt(prediction.before_mm)} → ${fmt(prediction.after_mm)} mm。`, 'field-note') : null);
    if (record.result?.status === 'RUN_COMPLETED') {
      const visual = node('div');box.append(visual);
      const metrics = append(node('div'),node('p','正在核对两版结果…','field-note'));
      const binding = {record_id:record.id,result_sha256:record.experiment_record?.result_sha256};
      const cacheKey = binding.record_id + ':' + binding.result_sha256;
      const read = () => {
        if (!comparedResults.has(cacheKey)) comparedResults.set(cacheKey,api('/learning/comparison',binding).catch(error=>{
          comparedResults.delete(cacheKey); throw error;
        }));
        comparedResults.get(cacheKey).then(summary=>{
          if (!metrics.isConnected) return;
          if(record.slit_followup && window.photonSlitComparison) {
            api('/cases/'+encodeURIComponent(summary.prior.record_id)).then(parent=>{
              if(!visual.isConnected)return;
              const prior=parent.result?.candidates.find(row=>row.id===summary.prior.candidate_id);
              const after=record.result?.candidates.find(row=>row.id===summary.current.candidate_id);
              if(parent.experiment_record?.result_sha256!==summary.prior.result_sha256 || !prior || !after)
                throw new Error('两版结果来源发生变化，请重新打开记录。');
              const view=window.photonSlitComparison({node,append,button,plot},prior,after,summary);
              if(view)visual.replaceChildren(view);
            }).catch(error=>visual.replaceChildren(node('p',error.message,'message error')));
          }
          const labels = {wavelength_nm:['波长','nm'],focal_length_mm:['焦距','mm'],slit_width_um:['缝宽','μm'],slit_height_um:['缝高','μm'],
            slit_separation_um:['中心距','μm'],relative_phase_rad:['相位','rad'],screen_distance_mm:['屏幕位置','mm'],
            waist_radius_um:['输入束腰半径','μm'],waist_z_mm:['输入束腰位置','mm'],grid_size:['网格',''],window_mm:['孔径窗口','mm']};
          const fixed = Object.entries(summary.retained).filter(([key])=>Object.hasOwn(labels,key)).map(([key,value])=>{
            const [name,unit] = labels[key]; return `${name} ${fmt(value)} ${unit}`.trim();
          });
          const verdict = value => value === true ? '达到目标' : value === false ? '未达到目标' : '按候选比较';
          const values = summary.goals.map(goal=>`${fmt(goal.value)} ${goal.unit || ''} · ${verdict(goal.met)}`);
          const title = {[FULL]:'横向中央全宽',[SIDE]:'中心至第一暗纹',central_intensity_ratio:'中心强度比',screen_radius_um:'屏幕半径'}[summary.goals[1].metric];
          metrics.replaceChildren(node('p','来源 ' + summary.prior.record_id.slice(0,8) + ' / ' + summary.prior.candidate_id +
            ' → 本版 ' + summary.current.record_id.slice(0,8) + ' / ' + summary.current.candidate_id,'field-note'),
            node('p','保持 · ' + fixed.join('，')), node('p','目标指标' + (title ? ' · ' + title : '') + ' · ' + values.join(' → ')),
            ...(summary.causes ? [changeCauses(summary.causes, {record_id:summary.prior.record_id,
              result_sha256:summary.prior.result_sha256,candidate_id:summary.prior.candidate_id})] : []),
            table(['观察量','来源','本版','依据'],summary.measures.map(row=>[row.label,`${fmt(row.before)} ${row.unit}`.trim(),`${fmt(row.after)} ${row.unit}`.trim(),row.basis])));
          if (summary.causes) metrics.append(button('恢复来源版本的效果',()=>{
            const controls = document.getElementById('slit-compensation');
            if (!controls) return;
            controls.open = true;
            controls.dispatchEvent(new CustomEvent('restore-slit-target',{detail:{...summary.prior,metric:summary.causes.metric}}));
            controls.scrollIntoView({block:'start'});
          },'small'));
          if (record.domain === 'diffraction') metrics.append(node('p','峰值归一化与分贝仅改变显示。上表峰值用共同入射光强比较，目标判定沿用原始指标。','field-note'));
        }).catch(error=>{ if (metrics.isConnected) metrics.replaceChildren(node('p',error.message,'field-note'),button('重新核对两版结果',read,'small')); });
      };
      const metadata=append(node('details'),node('summary','展开指标、保持项与运行依据'),metrics);
      box.append(metadata); read();
      const output = node('div');
      const show = button(record.domain === 'diffraction' ? '叠加来源与本次 FFT' : '叠加来源与本次曲线', async () => {
        show.disabled = true;
        try {
          const parent = await api('/cases/' + encodeURIComponent(prediction.source.record_id));
          const previous = parent.result?.candidates.find(row => row.id === prediction.candidate_id);
          const current = record.result.candidates[0];
          if (parent.experiment_record?.result_sha256 !== prediction.source.result_sha256 || !previous?.data?.series?.[0] || !current?.data?.series?.[0]) throw new Error('找不到版本匹配的两份曲线。');
          const series = [previous, current].map((row, index) => ({
            x:row.data.x, y:row.data.series[0].y,
            label:record.slit_followup
              ? `${index ? '本次' : '来源'} FFT · ${fmt(row.parameters.wavelength_nm)} nm · 缝宽 ${fmt(row.parameters.slit_width_um)} μm`
              : (index ? '本次 · ' : '来源 · ') + changes.map(change => change.label + ' ' + fmt(row.parameters[change.field]) + ' ' + change.unit).join('，')
          }));
          const graph = plot({x:series.flatMap(row => row.x), series, x_label:current.data.x_label, y_label:current.data.y_label});
          output.replaceChildren(graph);
          if (record.domain === 'gaussian') output.append(node('p',
            '屏幕半径由 ' + fmt(previous.metrics.screen_radius_um) + ' 变为 ' + fmt(current.metrics.screen_radius_um)
              + ' μm，新束腰位置为 ' + fmt(current.metrics.new_waist_z_mm) + ' mm。', 'learning-result'));
          output.scrollIntoView({block:'nearest'});
        } catch (error) { output.replaceChildren(node('p', error.message, 'message error')); }
        finally { show.disabled = false; }
      }, 'small');
      append(box, show, output);
    }
    host.append(box);
  }

  return {slitDesigner, tutor, comparison, designSummary, exploration, summaryLink, boundedSummary:bounded?.renderSummary};
};
