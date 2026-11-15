"use strict";

window.photonGuardApparatus = ({domain, parameters, plan = null, metrics = {}, modeCount = 0, source, links = [], onSelect}) => {
  const ns = 'http://www.w3.org/2000/svg';
  const make = (tag, attrs, text) => {
    const node = document.createElementNS(ns,tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key,value);
    if (text) node.textContent = text;
    return node;
  };
  const line = (x1,y1,x2,y2,cls) => make('line',{x1,y1,x2,y2,class:cls || 'apparatus-line'});
  const path = (d,cls) => make('path',{d,class:cls || 'apparatus-line'});
  const rect = (x,y,width,height,cls) => make('rect',{x,y,width,height,class:cls || 'apparatus-fill'});
  const label = (x,y,text) => make('text',{x,y,'text-anchor':'middle',class:'apparatus-label'},text);
  const svg = make('svg',{viewBox:'0 0 600 170',role:'group','aria-label':'光路与部件位置示意'});
  const panel = document.createElement('section'); panel.className = 'optical-apparatus';
  const heading = document.createElement('h3'); heading.textContent = '装置示意';
  const subtitle = document.createElement('p'); subtitle.className = 'field-note';
  subtitle.textContent = '结构示意 · 非按比例 · ' + source;
  const fields = document.createElement('div'); fields.className = 'apparatus-fields';
  const info = document.createElement('p'); info.className = 'apparatus-info'; info.setAttribute('aria-live','polite');
  info.textContent = '选择装置部件，查看它在当前实验中的作用。';
  const items = new Map();
  const fmt = value => Number.isFinite(value) ? Number(value.toPrecision(6)).toString() : '待核对';
  const describe = (key, unit, value = parameters[key]) => {
    const candidates = plan?.allowed_variables?.[key];
    if (candidates?.length > 1) return candidates.map(fmt).join(' / ') + (unit ? ' ' + unit : '');
    return Number.isFinite(value) ? fmt(value) + (unit ? ' ' + unit : '') : '待核对';
  };
  const component = (id, title, value, help, ...shapes) => {
    const group = make('g',{'data-apparatus-object':id,class:'apparatus-object'});
    group.append(...shapes); svg.append(group);
    const control = document.createElement('button'); control.type = 'button'; control.className = 'button small apparatus-field';
    control.dataset.apparatusParameter = id; control.setAttribute('aria-pressed','false');
    control.textContent = title + (value ? ' · ' + value : '');
    const locked = plan && (Array.isArray(plan.locked_parameters)
      ? plan.locked_parameters.includes(id) : Object.hasOwn(plan.locked_parameters || {}, id));
    items.set(id,{group,control,help:help + (locked ? ' 当前计划保持这一条件。' : '')});
    fields.append(control);
    const pick = () => { highlight(id); onSelect?.(id,title); };
    control.addEventListener('click',pick); group.addEventListener('click',pick);
  };
  function highlight(id, extra = '') {
    if (!items.has(id)) return;
    for (const [key, item] of items) {
      item.group.classList.toggle('is-selected',key === id);
      item.control.setAttribute('aria-pressed',String(key === id));
    }
    info.textContent = items.get(id).help + (extra ? ' ' + extra : '');
  }
  const lens = x => path(`M${x} 27 Q${x+23} 82 ${x} 137 Q${x-23} 82 ${x} 27 Z`,'apparatus-fill');
  const axis = () => svg.append(line(26,82,569,82,'apparatus-axis'));
  if (domain === 'diffraction') {
    const double = parameters.aperture === 'double_slit';
    axis();
    svg.append(path('M57 64 H146 M57 100 H146 M155 72 H284 L455 82 M155 92 H284 L455 82','apparatus-ray'));
    component('wavelength_nm','光源波长',describe('wavelength_nm','nm'),'波长影响焦平面上的衍射尺度。横向第一暗纹还取决于焦距和开口宽度。',
      rect(29,53,28,58),label(43,134,'相干光'));
    component('slit_width_um','缝宽',describe('slit_width_um','μm'),'横向开口宽度 a 对应横向包络。单缝第一暗纹距中心为 λf/a。',
      rect(145,32,10,37),rect(145,95,10,37),double ? rect(145,78,10,8) : line(141,69,141,95),label(150,151,double ? '双缝孔径' : '单缝孔径'));
    component('slit_height_um','缝高',describe('slit_height_um','μm'),'纵向开口高度决定理想焦平面的纵向尺度。看二维图时，可沿纵轴与横轴分别比较。',
      path('M181 56 h8 v52 h-8 M186 59 l3 -4 l3 4 M186 105 l3 4 l3 -4'),label(190,46,'纵向'));
    if (double) component('slit_separation_um','中心距',describe('slit_separation_um','μm'),'两条缝中心之间的距离影响相邻干涉条纹的周期。',
      path('M164 73 h8 v18 h-8'),label(192,130,'中心距'));
    component('focal_length_mm','焦距',describe('focal_length_mm','mm'),'焦距 f 为理想透镜到焦平面的距离。保持波长和缝宽时，增大焦距会放大横向尺度。',
      lens(284),path('M284 23 V15 H455 V23'),label(367,36,'焦距 f'),label(284,157,'薄透镜'));
    component('focal_plane','焦平面','', '场计算在焦平面给出横向中心曲线和二维光强。亮区全宽跨过左右两侧第一暗纹。',
      rect(450,40,10,85),path('M502 101 H555 l-6 -4 M502 101 V46 l-4 6'),label(521,38,'纵向 y'),label(539,121,'横向 x'),label(455,150,'焦平面'));
  } else if (domain === 'gaussian') {
    axis(); svg.append(path('M30 48 Q100 84 219 45 C276 47 336 72 370 74 C417 70 490 44 561 35 M30 116 Q100 80 219 119 C276 117 336 92 370 90 C417 94 490 120 561 129','apparatus-ray'));
    component('waist_radius_um','输入束腰',describe('waist_radius_um','μm'),'输入束腰是入射光束沿传播方向最细的位置。半径按同截面轴心强度的 1/e² 定义。',
      line(101,55,101,109),label(101,145,'输入束腰'));
    component('focal_length_mm','透镜焦距',describe('focal_length_mm','mm'),'薄透镜改变光束的传播条件。固定透镜和入射光束，移动屏幕时沿同一条传播曲线读值。',
      lens(219),label(219,157,'薄透镜'));
    component('new_waist_z_mm','新束腰位置',Number.isFinite(metrics.new_waist_z_mm) ? describe('new_waist_z_mm','mm',metrics.new_waist_z_mm) : '由计算确定',
      '新束腰对应透镜后半径曲线的最低点。',line(370,60,370,103),label(370,137,'新束腰'));
    component('screen_distance_mm','屏幕位置',describe('screen_distance_mm','mm'),'屏幕位置决定从哪一个截面读取光斑半径。传播曲线上的最低点对应束腰。',
      rect(502,34,9,97),label(506,151,'屏幕'));
  } else if (domain === 'film') {
    component('incident_index','入射介质折射率',describe('incident_index','',parameters.incident_index ?? parameters.n_incident),
      '入射介质与相邻膜层的折射率共同决定界面反射。',rect(50,46,165,78,'apparatus-medium'),label(130,146,'入射介质'));
    component('film_index','膜层折射率',describe('film_index','',parameters.film_index ?? parameters.layers?.[0]?.n),
      '光在膜层中传播产生相位积累。模型也使用膜层折射率计算两侧界面的反射。',rect(215,46,100,78,'apparatus-fill'),label(265,146,'膜层'));
    component('substrate_index','基底折射率',describe('substrate_index','',parameters.substrate_index ?? parameters.n_substrate),
      '基底位于膜层之后，基底和膜层的折射率差参与第二个界面的反射。',rect(315,46,218,78,'apparatus-medium'),label(425,146,'基底'));
    component('thickness_nm','膜厚',modeCount > 1 ? modeCount + ' 组候选' : describe('thickness_nm','nm',parameters.thickness_nm),
      '膜厚和波长共同影响反射率。单波长可能允许多组厚度，补测可进一步筛选候选。',
      path('M215 39 V22 H315 V39'),label(265,17,'膜厚 d'));
    svg.append(path('M74 85 H203 l-9 -5 M203 85 l-9 5 M205 72 H113 l9 -5 M113 72 l9 5','apparatus-ray'));
  } else return null;
  panel.append(heading,subtitle,svg,fields,info);
  if (links.length) {
    const actions = document.createElement('div'); actions.className = 'apparatus-fields';
    for (const link of links) {
      const jump = document.createElement('button'); jump.type = 'button'; jump.className = 'button small subtle';
      jump.textContent = link.label; jump.addEventListener('click',link.run); actions.append(jump);
    }
    panel.append(actions);
  }
  return {panel,highlight};
};
