"use strict";

window.photonGuardCsvIntake = ({api,node,append,button,consent}) => {
  const GAUSSIAN = 'gaussian_caustic_m2fixed1_v1';
  const FILM = 'single_film_known_index_v1';
  const unitNames = {mm:'mm',um:'μm',nm:'nm',m:'m',pixel:'像素',percent:'%',fraction:'0–1',relative:'相对量'};
  const num = value => Number.isFinite(value) ? Number(value.toPrecision(8)).toString() : '—';
  const label = (text, input) => {
    input.setAttribute('aria-label',text);
    return append(node('label',null,'learning-field'),node('span',text),input);
  };
  const choose = (items, value) => {
    const input = node('select');
    for (const [id,text] of items) { const option = node('option',text); option.value = id; input.append(option); }
    input.value = value ?? '';
    return input;
  };
  function table(head, rows) {
    const header = append(node('thead'),append(node('tr'),...head.map(value=>node('th',value))));
    const body = append(node('tbody'),...rows.map(row=>append(node('tr'),...row.map(value=>node('td',value)))));
    return append(node('div',null,'table-scroll'),append(node('table'),header,body));
  }
  const columnName = column => `${column.index+1}. ${column.name}`;
  const originalUrl = (record,index) => '/api/experiments/cases/' + encodeURIComponent(record.id) + '/csv/' + index;

  function render(host, {getModel, modelControl, submit, existing}) {
    const root = node('div',null,'csv-intake');
    const initial = node('div');
    const overview = node('div');
    const editor = node('div');
    const result = node('div'); result.setAttribute('aria-live','polite');
    const error = node('p',null,'field-note'); error.setAttribute('role','status');
    const metadata = node('div',null,'learning-control-grid');
    const source = node('input'); source.maxLength = 300; source.placeholder = '例如：本次教学合成扫描或仪器导出文件';
    const kind = choose([['USER_PROVIDED','自己的数据'],['LICENSED_PUBLIC','有许可的公开数据'],
      ['SYNTHETIC_TEACHING','合成教学数据']],'USER_PROVIDED');
    const license = node('input'); license.maxLength = 300; license.placeholder = '许可或授权说明';
    const licenseField = label('数据许可',license); licenseField.hidden = true;
    const permit = consent(existing ? '确认另存这份列解释，随后重新核对分析计划。' : '确认保存原始文件和当前列解释。');
    let text = '', version = 0, pending = false, approved = null, read = null, check = null;
    const action = button(existing ? '另存数据解释版本' : '保存数据并核对分析条件',async()=>{
      if (!approved || !permit.input.checked || pending || !read) return;
      const mapping = read(); pending = true; update();
      try {
        await submit({csv_mapping:mapping,csv_preview_sha256:approved,approve:true,
          ...(existing ? {group_index:existing.index} : {csv_text:text,source_kind:kind.value,source_name:source.value.trim(),license:license.value.trim()})});
      } catch (err) { error.textContent = err.message; }
      finally { pending = false; update(); }
    },'primary',true);
    const confirmBox = append(node('div'),existing ? null : metadata,permit.wrap,action); confirmBox.hidden = true;
    if (!existing) append(metadata,label('数据来源类型',kind),label('数据来源说明',source),licenseField);
    function update() {
      licenseField.hidden = kind.value !== 'LICENSED_PUBLIC';
      if (check) check.disabled = pending || !read?.();
      action.disabled = pending || !approved || !permit.input.checked || !existing && (!source.value.trim() || kind.value === 'LICENSED_PUBLIC' && !license.value.trim());
    }
    function invalidate() { version++; approved = null; permit.input.checked = false; confirmBox.hidden = true; result.replaceChildren(); error.textContent = ''; update(); }
    const changed = () => invalidate();
    async function inspect(currentMapping = null) {
      const stamp = ++version;
      approved = null; read = null; check = null; confirmBox.hidden = true; permit.input.checked = false;
      overview.replaceChildren(); editor.replaceChildren(); result.replaceChildren(); error.textContent = '';
      if (!text) return;
      pending = true; error.textContent = '正在读取列与数据范围…'; update();
      try {
        const data = await api('/csv/preview',{csv_text:text,model:getModel()});
        if (stamp !== version || !root.isConnected) return;
        const separator = {',':'逗号',';':'分号','\t':'制表符'}[data.delimiter];
        const summary = table(['原始列','表头单位','数值范围','缺失','非数值'],data.columns.map(column=>[columnName(column),unitNames[column.unit_hint] || '待确认',
          column.range?.map(num).join(' – ') || '—',column.missing,column.nonnumeric]));
        summary.tabIndex = 0; summary.setAttribute('aria-label','原始列概览，可横向滚动');
        append(overview,node('h3','原文件概览'),node('p',`${data.rows} 条记录 · ${data.columns.length} 列 · 分隔符为${separator}`),summary,
          node('p','横向滑动可查看完整列信息。','field-note csv-narrow-hint'),
          data.shape_error_count ? node('p',`列数不一致 ${data.shape_error_count} 行，先核对原始行 ${data.shape_errors.join('、')}。`,'field-note') : null,
          append(node('details'),node('summary','查看原始行预览'),table(['原始行',...data.columns.map(columnName)],
            data.samples.map(sample=>[sample.line,...data.columns.map(column=>sample.cells[column.index] ?? '缺项')]))));
        mappingEditor(data,currentMapping || data.suggestion);
        error.textContent = '';
      } catch (err) { if (stamp === version) error.textContent = err.message; }
      finally { if (stamp === version) { pending = false; update(); } }
    }

    function mappingEditor(data, initialValues) {
      const model = getModel(), gauss = model === GAUSSIAN, film = model === FILM;
      const defaultColumn = [['','请选择列'],...data.columns.map(column=>[String(column.index),columnName(column)])];
      const units = keys => [['','请选择单位'],...keys.map(key=>[key,unitNames[key]])];
      const lengths = ['mm','um','nm','m'];
      const axis = choose(defaultColumn,initialValues.x);
      const axisUnit = choose(units(lengths),initialValues.x_unit);
      const y = choose(defaultColumn,initialValues.y);
      const yUnit = choose(units(gauss ? lengths : film ? ['fraction','percent'] : ['relative']),initialValues.y_unit || (!gauss && !film ? 'relative' : ''));
      const width = choose([['','请选择光斑口径'],['radius','1/e² 强度半径'],['diameter','1/e² 强度直径']],initialValues.width_kind);
      const sd = choose([['','不使用标准差列'],...defaultColumn.slice(1)],initialValues.sigma);
      const sdUnit = choose(units(lengths),initialValues.sigma_unit);
      const sdField = label('标准差单位',sdUnit); sdField.hidden = !sd.value;
      sd.addEventListener('change',()=>{sdField.hidden=!sd.value;});
      const meaning = consent(gauss ? '确认宽度及所用标准差采用所选口径。直径和直径标准差会同时除以 2。'
        : '确认这列是已校准反射率。若用百分比，分析时转为 0–1。');
      meaning.input.checked = initialValues.meaning_confirmed === true;
      const sorted = consent('明确按物理坐标重新排序，保留所有点。'); sorted.input.checked = initialValues.sort === true;
      const omitted = node('input'); omitted.placeholder = '例如 14, 18'; omitted.value = (initialValues.exclude_rows || []).join(', ');
      const why = node('input'); why.maxLength = 300; why.value = initialValues.exclude_reason || '';
      const knownUnits = input => input.value || null;
      read = () => {
        if (!axis.value || !y.value || axis.value === y.value || !axisUnit.value || !yUnit.value || (film || gauss) && !meaning.input.checked || gauss && (!width.value || sd.value && !sdUnit.value)) return null;
        const rawRows = omitted.value.trim().split(/[,，、;；\s]+/).filter(Boolean);
        if (rawRows.some(row=>!/^\d+$/.test(row)) || rawRows.length && !why.value.trim()) return null;
        return {x:Number(axis.value),y:Number(y.value),x_unit:axisUnit.value,y_unit:yUnit.value,sort:sorted.input.checked,
          exclude_rows:rawRows.map(Number),exclude_reason:why.value.trim(),
          ...(gauss ? {width_kind:width.value,sigma:sd.value ? Number(sd.value) : null,sigma_unit:knownUnits(sdUnit),meaning_confirmed:meaning.input.checked}
            : film ? {meaning_confirmed:meaning.input.checked} : {})};
      };
      const form = append(node('div'),append(node('div',null,'learning-control-grid'),
        label(film ? '波长对应列' : '位置对应列',axis),label(film ? '波长单位' : '位置单位',axisUnit),
        label(gauss ? '光斑宽度对应列' : film ? '反射率对应列' : '相对光强对应列',y),label(gauss ? '光斑宽度单位' : film ? '反射率表示' : '光强表示',yUnit)),
        gauss ? append(node('div',null,'learning-control-grid'),label('光斑宽度定义',width),label('标准差对应列（可选）',sd),sdField) : null,
        gauss || film ? meaning.wrap : null,
        append(node('details'),node('summary','需要排序或明确排除某些原始行时'),sorted.wrap,
          label('排除的原始行号',omitted),label('排除理由',why),node('p','只有明确列出的行会从分析副本排除，原文件完整保留。','field-note')));
      check = button('预览分析数据',async()=>{
        const mapping = read();
        if (!mapping || pending) return;
        const stamp = version; pending = true; approved = null; permit.input.checked = false; confirmBox.hidden = true;
        result.replaceChildren();error.textContent = '';update();
        try {
          const report = await api('/csv/preview',{csv_text:text,model,mapping});
          if (stamp !== version || !root.isConnected) return;
          const mapped = report.mapped;
          append(result,node('h3','分析副本预览'),node('p',`原始 ${mapped.original_rows} 条 · 分析 ${mapped.mapped_rows} 条 · 明确排除 ${mapped.excluded_rows.length} 条`),
            table(['原始列','分析列','转换系数'],mapped.transformations.map(item=>[columnName(data.columns[item.source_column]),item.target_column,item.factor])),
            ...mapped.issues.map(issue=>node('p',issue.text + (issue.rows.length ? ` · 原始行 ${issue.rows.join('、')}` : ''),'inline-notice')),
            ...mapped.warnings.map(warning=>node('p',warning,'field-note')),
            mapped.outlier_rows.length ? node('p','疑似突变行：'+mapped.outlier_rows.join('、'),'field-note') : null,
            table(['原始行',...mapped.columns],mapped.samples.map(sample=>[sample.line,...sample.values.map(num)])));
          if (mapped.can_save) { approved = mapped.sha256; confirmBox.hidden = false; }
        } catch (err) { if (stamp === version) error.textContent = err.message; }
        finally { pending = false; update(); }
      },'small',true);
      form.addEventListener('input',changed); form.addEventListener('change',changed);
      append(editor,node('h3','解释每一列'),node('p','核对列与单位。预览会说明转换，原始文件继续完整保留。','field-note'),form,check);
    }
    if (existing) {
      const load = button('读取本次原始文件',async()=>{
        if (pending) return;
        pending = true; load.disabled = true;
        try {
          const response = await fetch(originalUrl(existing.record,existing.index),{credentials:'same-origin',cache:'no-store'});
          if (!response.ok) throw new Error('原始文件无法读取，请核对当前记录。');
          text = new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await response.arrayBuffer());
          await inspect(existing.record.groups[existing.index].intake?.mapping);
        } catch (err) { error.textContent = err.message; }
        finally { pending = false; load.disabled = false; }
      },'small');
      initial.append(load);
    } else {
      const file = node('input'); file.type = 'file'; file.accept = '.csv,.tsv,text/csv,text/tab-separated-values'; file.setAttribute('aria-label','反向分析 CSV');
      file.addEventListener('change',async()=>{
        const stamp = ++version, selected = file.files?.[0];
        text = ''; pending = false;
        approved = null; read = null; check = null; confirmBox.hidden = true; overview.replaceChildren(); editor.replaceChildren(); result.replaceChildren(); error.textContent = '';
        if (!selected) return;
        if (selected.size > 2*1024*1024) { error.textContent = '文件超过 2 MiB。'; return; }
        try {
          const decoded = new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await selected.arrayBuffer());
          if (stamp !== version) return;
          text = decoded; if (!source.value.trim()) source.value = selected.name.slice(0,300);
          await inspect();
        } catch (_) { if (stamp === version) error.textContent = '请使用 UTF-8 编码的 CSV。'; }
      });
      initial.append(file);
      modelControl?.addEventListener('change',()=>{if(text) inspect();});
    }
    for (const element of [permit.input,source,kind,license]) element.addEventListener('input',update);
    append(root,initial,overview,editor,result,confirmBox,error); host.append(root);
  }

  function saved(host, record, pending, revise, addGroup) {
    const panel = append(node('details',null,'card'),node('summary','查看原始 CSV 与列解释'));
    for (const [index,group] of record.groups.entries()) {
      const audit = group.intake;
      const area = append(node('section'),node('h3',`采集 ${index+1} · ${group.dataset.source_name}`));
      const download = node('a','下载原始 CSV','button small'); download.href=originalUrl(record,index); download.download=''; area.append(download);
      if (audit) {
        const analysis = node('a','下载分析副本','button small'); analysis.href=originalUrl(record,index)+'/analysis'; analysis.download=''; area.append(analysis);
        append(area,node('p',`原始 ${audit.original_rows} 条 · 分析 ${audit.mapped_rows} 条 · 原始文件指纹 ${audit.original_sha256.slice(0,12)}`,'field-note'),
          table(['原始列','分析列','转换系数'],audit.transformations.map(row=>[columnName(audit.original_columns[row.source_column]),row.target_column,row.factor])),
          audit.excluded_rows.length ? node('p',`明确排除原始行 ${audit.excluded_rows.join('、')} · ${audit.mapping.exclude_reason}`,'field-note') : null);
      }
      if (!record.teaching_object && record.status !== 'RUNNING' && !pending) {
        const edit = append(node('details'),node('summary','从原文件另存列解释'));
        render(edit,{getModel:()=>record.inverse_settings.model,existing:{record,index},submit:revise});
        area.append(edit);
      }
      panel.append(area);
    }
    host.append(panel);
    if (!record.teaching_object && record.status === 'COMPLETED' && record.inverse_result && record.groups.length < 3 && !pending) {
      const next = append(node('details',null,'card'),node('summary','导入下一份 CSV'),
        node('p','分别核对新采集的列、单位和数据来源。保存后再确认各组条件及残差尺度。','field-note'));
      next.id = 'csv-add-group';
      render(next,{getModel:()=>record.inverse_settings.model,submit:addGroup});
      host.append(next);
    }
  }

  return {render,saved};
};
