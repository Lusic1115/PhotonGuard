"use strict";

window.photonGuardBoundedGoal = ({api,node,append,button,consent,openCase,aiConnected,isCredential}) => {
  const fmt = value => Number.isFinite(value) ? Number(value.toPrecision(7)).toString() : '—';
  const labels = {wavelength_nm:'波长',slit_width_um:'缝宽',focal_length_mm:'焦距',screen_distance_mm:'屏幕位置',
    layer_count:'层数',thickness_nm:'膜厚',layers:'膜厚'};
  const units = {wavelength_nm:'nm',slit_width_um:'μm',focal_length_mm:'mm',screen_distance_mm:'mm',thickness_nm:'nm'};
  const FULL = 'central_zero_to_zero_width_mm', SIDE = 'first_zero_mm';
  const metrics = {[FULL]:['横向中央全宽','mm'],[SIDE]:['中心到单侧第一暗纹','mm'],screen_radius_um:['屏幕光斑半径','μm'],
    new_waist_z_mm:['新束腰位置','mm'],reflectance:['反射率',''],transmittance:['透射率','']};
  const select = rows => {
    const input = node('select');
    for (const [id, label] of rows) { const option = node('option',label); option.value = id; input.append(option); }
    return input;
  };
  const field = (label, input) => {
    input.setAttribute('aria-label',label);
    return append(node('label',null,'learning-field'),node('span',label),input);
  };
  const number = (value, min, max) => {
    const input = node('input'); input.type = 'number'; input.step = 'any';
    if (min != null) input.min = min;
    if (max != null) input.max = max;
    input.value = Number.isFinite(value) ? value : '';
    return input;
  };
  const readNumber = input => input.value !== '' && input.checkValidity() ? Number(input.value) : null;
  const fixed = {fixed:'保持当前值',source:'沿用来源限制',catalog:'填写可用目录',range:'填写允许范围'};

  function scopeField(label, defaultMode, allowed) {
    const chooser = select([['','请选择条件'],...allowed.map(mode=>[mode,fixed[mode]])]); chooser.value = defaultMode;
    const catalog = node('input'); catalog.type = 'text'; catalog.placeholder = '用逗号分隔，例如 24, 26, 28';
    const lower = number(), upper = number();
    const list = field(label + '目录',catalog);
    const range = append(node('div',null,'learning-control-grid'),field(label + '下限',lower),field(label + '上限',upper));
    const block = append(node('div'),field(label + '限制',chooser),list,range);
    function update() { list.hidden = chooser.value !== 'catalog'; range.hidden = chooser.value !== 'range'; }
    function apply(scope) {
      chooser.value = scope?.kind || '';
      if (scope?.kind === 'catalog') catalog.value = scope.values.join(', ');
      if (scope?.kind === 'range') { lower.value = scope.min; upper.value = scope.max; }
      update();
    }
    function read() {
      const kind = chooser.value;
      if (kind === 'catalog') {
        const values = catalog.value.trim().split(/[,，、;；\s]+/);
        return values.length && values.every(value=>/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value)) ? {kind,values:values.map(Number)} : null;
      }
      if (kind === 'range') return readNumber(lower) !== null && readNumber(upper) !== null ? {kind,min:readNumber(lower),max:readNumber(upper)} : null;
      return kind ? {kind} : null;
    }
    chooser.addEventListener('change',update); update();
    return {block, apply, read};
  }

  function limitsText(limits) {
    return Object.entries(limits).map(([key, scope])=>{
      const unit = units[key] || '';
      const values = scope.kind === 'range' ? (scope.min <= scope.max ? `${fmt(scope.min)}–${fmt(scope.max)}` : '无交集') : scope.values.map(fmt).join('、') || '无交集';
      return `${labels[key] || key} ${values}${unit && values !== '无交集' ? ' ' + unit : ''}`;
    }).join(' · ');
  }

  function goalText(goal) {
    const [label, unit] = metrics[goal.metric];
    const wavelength = goal.wavelength_um == null ? '' : `${fmt(goal.wavelength_um*1000)} nm 处的`;
    if (goal.operator === 'minimize') return `在给定条件中比较最小${label}`;
    const operator = {near:'', '>=':'≥ ', '<=':'≤ '}[goal.operator];
    return `${wavelength}${label} ${operator}${fmt(goal.target)}${goal.operator === 'near' ? ' ± ' + fmt(goal.tolerance) : ''}${unit ? ' ' + unit : ''}`;
  }

  function statusText(summary) {
    if (summary.status === 'NO_MATCH') return '当前目录或范围内未找到达标项。下方保留最接近的一项供核对。';
    if (summary.status === 'NO_LEGAL_CANDIDATE') return '原约束、当前可用条件与模型范围内没有可计算的交集。请核对条件后再探索。';
    if (summary.preferred_available) return `保持原${labels[summary.held_preference]}时已有达标项。`;
    if (summary.held_preference && summary.status === 'FOUND') return `保持原${labels[summary.held_preference]}时没有达标项。下方是可用范围内的备选。`;
    return summary.status === 'FOUND' ? '下方候选满足预览目标。' : '下方为有限候选中的排序。';
  }

  function rowsTable(summary, selection, actual = null) {
    const [metric, unit] = metrics[summary.goal.metric];
    const hasObservation = summary.domain !== 'film';
    const body = node('tbody');
    for (const [index,row] of summary.rows.entries()) {
      const chosen = selection(row);
      const choices = Object.entries(row.values).map(([key,value])=>key === 'layers' ? `膜厚 ${fmt(row.details.thickness_nm)} nm`
        : `${labels[key]} ${fmt(value)}${units[key] ? ' ' + units[key] : ''}`).join(' · ');
      const info = summary.domain === 'gaussian' ? `屏幕半径 ${fmt(row.details.screen_radius_um)} μm · 新束腰 ${fmt(row.details.new_waist_z_mm)} mm`
        : summary.domain === 'diffraction' ? `纵向全宽 ${fmt(row.details.vertical_width_mm)} mm` : '';
      const goal = actual?.[index]?.goal;
      const verdict = goal?.met === true ? ' · 达到目标' : goal?.met === false ? ' · 未达目标' : '';
      const measured = actual ? node('td',goal && actual[index].all_checks_passed ? `${fmt(goal.value)} ${unit}${verdict}`.trim() : '查看数值检查') : null;
      append(body,append(node('tr'),append(node('td'),chosen),node('td',choices),node('td',`${fmt(row.value)} ${unit}`.trim()),measured,hasObservation ? node('td',info) : null));
    }
    return append(node('div',null,'table-scroll'),append(node('table'),append(node('thead'),append(node('tr'),
      ...['候选','本轮方案',`${metric} · 预览`,...(actual ? ['实际判定'] : []),...(hasObservation ? ['另一个观察量'] : [])].map(text=>node('th',text)))),body));
  }

  function controls(host, record, candidate) {
    const {domain} = record;
    const single = candidate.parameters?.aperture === 'single_slit';
    const params = record.spec?.known_parameters || {};
    const filmType = !params.layers && params.layer_count ? 'layer_count' : params.layers?.length === 1 ? 'thickness_nm' : null;
    if (!single && domain !== 'gaussian' && !(domain === 'film' && filmType)) return;
    const panel = append(node('details',null,'learning-controls'),node('summary',single ? '按可用条件保持或恢复目标' : '按可用条件比较方案'));
    panel.id = single ? 'slit-compensation' : 'bounded-planning';
    const form = node('div');
    const output = node('div'); output.setAttribute('aria-live','polite');
    const error = node('p',null,'field-note'); error.setAttribute('role','status');
    const binding = {record_id:record.id,result_sha256:record.experiment_record?.result_sha256,candidate_id:candidate.id};
    const p = candidate.parameters;
    let read, waiting = false, edit = 0, requestText = '', lastQuotes = {};
    const headline = node('p',`参照 ${record.id.slice(0,8)} · ${candidate.id}。先核对条件，再筛选少量候选。`,'field-note');
    panel.append(headline);

    if (single) {
      const wave = number(p.wavelength_nm === 532 ? 633 : 532,200,2000);
      const target = number(candidate.metrics.first_zero_mm * 2,0.000001,40000);
      const tolerance = number(record.spec?.goal?.metric === FULL ? record.spec.goal.tolerance : null,0,40000);
      const metric = select([[FULL,metrics[FULL][0]],[SIDE,metrics[SIDE][0]]]);
      const versions = (record.experiment_record?.available_versions || []).filter(version=>version.id !== record.id);
      const referenceVersion = select([['','填写目标值'],...versions.map(version=>[version.id,'使用保存结果 · ' + version.id.slice(0,8)])]);
      const referenceCandidate = select([]);
      const referenceField = field('恢复参照候选',referenceCandidate);
      const targetField = field('目标值（mm）',target);
      let reference = null;
      referenceField.hidden = true;
      async function chooseReference(id, candidateId = '') {
        reference = null;
        referenceCandidate.replaceChildren();
        referenceField.hidden = !id;
        targetField.hidden = !!id;
        invalidate();
        if (!id) return;
        try {
          const source = await api('/cases/' + encodeURIComponent(id));
          if (!panel.isConnected || referenceVersion.value !== id) return;
          if (!source.result || !source.experiment_record?.result_sha256) throw new Error('该版没有保存结果，请选其他版本。');
          const rows = source.result.candidates.filter(row=>row.all_checks_passed === true && row.parameters?.aperture === 'single_slit');
          if (!rows.length) throw new Error('该版没有可用的单缝候选。');
          referenceCandidate.append(...Array.from(select([['','请选择一块参照缝片'],
            ...rows.map(row=>[row.id,`${fmt(row.parameters.slit_width_um)} μm · ${row.id}`])]).children));
          referenceCandidate.value = candidateId || (rows.length === 1 ? rows[0].id : '');
          reference = {record_id:id,result_sha256:source.experiment_record.result_sha256};
        } catch (err) { if (referenceVersion.value === id) error.textContent = err.message; }
        finally { update(); }
      }
      referenceVersion.addEventListener('change',()=>chooseReference(referenceVersion.value));
      metric.addEventListener('change',()=>{ if (!referenceVersion.value) target.value = candidate.metrics.first_zero_mm * (metric.value === FULL ? 2 : 1); });
      const width = scopeField('缝宽（μm）','source',['source','fixed','catalog','range']);
      const focal = scopeField('焦距（mm）','fixed',['fixed','catalog','range']);
      const pref = select([['keep_focus','优先保持焦距'],['keep_width','优先保持缝宽'],['balanced','按目标比较']]);
      append(form,append(node('div',null,'learning-control-grid'),field('新波长（nm）',wave),field('评价尺度',metric),
        field('允许误差（mm）',tolerance),field('恢复目标来源',referenceVersion),referenceField,targetField),
        append(node('div',null,'learning-control-grid'),width.block,focal.block),field('排序偏好',pref));
      read = () => ({wavelength_nm:readNumber(wave),metric:metric.value,tolerance:readNumber(tolerance),
        ...(referenceVersion.value ? {target_reference:reference && referenceCandidate.value
          ? {...reference,candidate_id:referenceCandidate.value} : null} : {target:readNumber(target)}),
        width_scope:width.read(),focal_scope:focal.read(),preference:pref.value});
      panel.addEventListener('restore-slit-target',event=>{
        const source = event.detail;
        if (![...referenceVersion.options].some(option=>option.value===source.record_id)) {
          referenceVersion.append(...Array.from(select([[source.record_id,'使用保存结果 · ' + source.record_id.slice(0,8)]]).children));
        }
        metric.value = source.metric;
        wave.value = p.wavelength_nm;
        tolerance.value = '';
        width.apply({kind:'source'});
        focal.apply({kind:'fixed'});
        referenceVersion.value = source.record_id;
        chooseReference(source.record_id,source.candidate_id);
      });
      const question = node('textarea'); question.rows = 2; question.maxLength = 1600;
      question.placeholder = '例如：换成 532 nm，保持原来的全宽。只有 24、26、28 μm 缝片，焦距固定。';
      const report = node('div'); report.setAttribute('role','status');
      const permit = consent('允许把这段条件发给已连接的 AI。');
      const intake = async mode => {
        if (waiting || !question.value.trim() || isCredential(question.value)) return;
        const text = question.value.trim(), version = edit;
        waiting = true; update();
        try {
          const value = await api('/learning/bounded/inspect',{...binding,text,mode,...(mode === 'live' ? {approve_data:permit.input.checked} : {})});
          if (!panel.isConnected || edit !== version || text !== question.value.trim()) return;
          const v = value.values, q = value.questions;
          if (v.metric) metric.value = v.metric;
          for (const [key, input] of Object.entries({wavelength_nm:wave,target,tolerance})) {
            if (q[key]) input.value = '';
            else if (Number.isFinite(v[key])) input.value = v[key];
          }
          if (v.width_scope || q.width_scope) width.apply(v.width_scope);
          if (v.focal_scope || q.focal_scope) focal.apply(v.focal_scope);
          if (v.preference) pref.value = v.preference;
          if (value.restore_reference && !referenceVersion.value && !v.metric && record.spec?.goal?.metric === SIDE)
            metric.value = SIDE;
          requestText = text; lastQuotes = value.quotes;
          output.replaceChildren();
          const label = value.origin === 'LIVE_AI' ? `DeepSeek 已读取原文，补充 ${value.ai_filled.length} 项。请核对表单。`
            : value.origin === 'LOCAL_AFTER_AI' ? '已使用可核对的本地提取。请核对表单。' : '已整理能核对的原文条件。';
          report.replaceChildren(node('p',label,'field-note'),...Object.values(q).map(note=>node('p',note,'field-note')));
          if (Object.keys(lastQuotes).length) {
            const quotes = append(node('details'),node('summary','查看原文依据'));
            for (const quote of new Set(Object.values(lastQuotes))) quotes.append(node('p',quote,'field-note'));
            report.append(quotes);
          }
        } catch (err) { if (edit === version) report.replaceChildren(node('p',err.message,'field-note')); }
        finally { waiting = false; update(); refreshRead(); }
      };
      const local = button('本地整理条件',()=>intake('local'),'small',true);
      const live = button('请 AI 整理条件',()=>intake('live'),'small',true);
      const refreshRead = () => {
        const available = !waiting && question.value.trim() && !isCredential(question.value);
        local.disabled = !available; live.disabled = !available || !permit.input.checked;
      };
      question.addEventListener('input',()=>{edit++; requestText = ''; lastQuotes = {}; output.replaceChildren(); report.replaceChildren(); refreshRead(); update();});
      permit.input.addEventListener('change',refreshRead);
      panel.append(append(node('details'),node('summary','用一句话说明条件'),field('这轮的目标和限制',question),
        aiConnected() ? permit.wrap : null,append(node('div',null,'actions'),local,aiConnected() ? live : null),report));
    } else if (domain === 'gaussian') {
      const focal = scopeField('透镜焦距（mm）','source',['source','fixed','catalog']);
      const screen = scopeField('屏幕位置（mm）','fixed',['fixed','catalog','range']);
      const metric = select([['min_radius','比较最小屏幕光斑'],['radius_limit','屏幕光斑半径上限'],['waist_position','新束腰位置']]);
      const pref = select([['balanced','按目标比较'],['keep_focus','优先保持透镜'],['keep_screen','优先保持屏幕']]);
      const target = number(), tolerance = number(1,0);
      const targetField = field('目标值（见指标单位）',target), toleranceField = field('束腰位置容差（mm）',tolerance);
      const updateTarget = () => { targetField.hidden = metric.value === 'min_radius'; toleranceField.hidden = metric.value !== 'waist_position'; };
      metric.addEventListener('change',updateTarget); updateTarget();
      if (record.kind === 'teaching' && !record.parent_id && record.learning_lesson?.id === 'gaussian-waist') {
        form.append(node('p','可沿用教学透镜目录，也可以填写手头的透镜。后续计划以本轮核对的目录为准。','field-note'));
      }
      append(form,append(node('div',null,'learning-control-grid'),focal.block,screen.block),field('比较目标',metric),targetField,toleranceField,field('排序偏好',pref));
      read = () => ({focal_scope:focal.read(),screen_scope:screen.read(),metric:metric.value,preference:pref.value,
        ...(metric.value !== 'min_radius' ? {target:readNumber(target)} : {}),...(metric.value === 'waist_position' ? {tolerance:readNumber(tolerance)} : {})});
    } else {
      const scope = scopeField(filmType === 'layer_count' ? '层数' : '膜厚（nm）',filmType === 'layer_count' ? 'source' : 'catalog',
        filmType === 'layer_count' ? ['source','catalog','fixed'] : ['catalog','fixed']);
      const metric = select([['reflectance','反射率'],['transmittance','透射率']]);
      const pref = select([['fewer_layers','达标时优先少层数'],['balanced','按目标比较']]);
      const wave = number((record.spec?.goal?.wavelength_um || params.design_wavelength_um || p.wavelengths_um?.[0]) * 1000,500,5000);
      const target = number(record.spec?.goal?.target,0,1);
      append(form,scope.block,append(node('div',null,'learning-control-grid'),field('评价波长（nm）',wave),field('最小目标比例（0–1）',target)),
        field('比较指标',metric),filmType === 'layer_count' ? field('排序偏好',pref) : null);
      read = () => ({variable:filmType,scope:scope.read(),wavelength_nm:readNumber(wave),target:readNumber(target),metric:metric.value,preference:pref.value});
    }

    function update() { preview.disabled = waiting || Object.values(read()).some(value=>value === null); }
    function invalidate() { edit++; output.replaceChildren(); error.textContent = ''; update(); }
    const preview = button(single ? '预览保宽方案' : '预览受限候选',async()=>{
      const request = read(), version = edit;
      if (waiting || Object.values(request).some(value=>value === null)) return;
      waiting = true; update(); output.replaceChildren(); error.textContent = '';
      const input = requestText ? {input_text:requestText,input_quotes:lastQuotes} : {};
      try {
        const summary = await api('/learning/bounded/preview',{...binding,request,...input});
        if (edit !== version || !panel.isConnected) return;
        const closest = summary.status === 'NO_MATCH';
        append(output,node('p',statusText(summary),'learning-result'),node('p',goalText(summary.goal)),
          summary.target_reference ? node('p',`恢复目标取自 ${summary.target_reference.record_id.slice(0,8)} · ${summary.target_reference.candidate_id}。`,'field-note') : null,
          Number.isFinite(summary.ideal_width_um) ? node('p',`固定波长和焦距时，解析建议缝宽 ${fmt(summary.ideal_width_um)} μm。`,'field-note') : null,
          node('p','本轮硬限制 · ' + limitsText(summary.limits),'field-note'),
          node('p','可计算范围 · ' + limitsText(summary.effective_limits),'field-note'),
          node('p',`${summary.method} · 已筛选 ${summary.screened_candidates} 项 · 尚未启动批准的计算`,'field-note'));
        if (closest) output.append(node('p',single
          ? '如有另一块可用缝片，核对目录。若焦距可调，明确允许范围后再预览。超出来源范围的条件可以另建实验。'
          : '可核对当前目录或目标。只有在条件确实允许变化时，才填写新的可用值并重新预览。','field-note'));
        if (!summary.rows.length) return;
        const selected = new Set(closest ? [] : [summary.rows[0].id]);
        const label = closest ? '验证最接近项' : '验证所选方案';
        const prepare = button((summary.new_round_required ? '另开一轮，' : '准备') + label,async()=>{
          if (waiting || edit !== version || !selected.size) return;
          waiting = true; prepare.disabled = true; update();
          try {
            const saved = await api('/learning/bounded/plan',{...binding,request,...input,preview_sha256:summary.sha256,
              candidate_ids:[...selected],confirm_plan:true,verify_closest:closest,
              ...(summary.new_round_required ? {new_round:true} : {})});
            if (panel.isConnected && edit === version && saved.case) openCase(saved.case,'已保存受限方案。核对计划后再批准。');
          } catch (err) { error.textContent = err.message; }
          finally { waiting = false; prepare.disabled = !selected.size; update(); }
        },'primary',!selected.size);
        output.append(rowsTable(summary,row=>{
          const pick = node('input'); pick.type = 'checkbox'; pick.checked = selected.has(row.id);
          pick.setAttribute('aria-label','选择方案 ' + row.id);
          pick.addEventListener('change',()=>{ if (pick.checked) selected.add(row.id); else selected.delete(row.id); prepare.disabled = waiting || !selected.size; });
          return pick;
        }),prepare);
      } catch (err) { if (edit === version) error.textContent = err.message; }
      finally { waiting = false; update(); }
    },'small',true);
    form.addEventListener('input',invalidate); form.addEventListener('change',invalidate);
    append(panel,form,preview,output,error); host.append(panel); update();
  }

  function renderSummary(host, record) {
    const summary = record.bounded_goal;
    if (!summary) return;
    const ids = new Set(summary.chosen_ids);
    const section = append(node('section',null,'learning-preview'),node('h3','本轮受限方案'),
      node('p',`沿用条件 ${summary.source.record_id.slice(0,8)} · ${summary.source.candidate_id} · ${goalText(summary.goal)}`),
      summary.target_reference ? node('p',`恢复目标取自 ${summary.target_reference.record_id.slice(0,8)} · ${summary.target_reference.candidate_id}。`,'field-note') : null,
      node('p','可计算范围 · ' + limitsText(summary.effective_limits),'field-note'),
      node('p',statusText(summary),'field-note'),
      rowsTable({...summary,rows:summary.rows.filter(row=>ids.has(row.id))},row=>node('span',row.id),record.result?.candidates),
      node('p',record.result ? `已执行快算 ${record.result.resources.fast_evaluations} 次。实际结果见候选表。`
        : `所选 ${ids.size} 项预计快算 ${ids.size * summary.fast_cost_per_candidate} 次。批准后执行。`,'field-note'));
    if (summary.input_text) section.append(append(node('details'),node('summary','本轮输入原文'),node('p',summary.input_text)));
    host.append(section);
  }
  return {controls,renderSummary};
};
