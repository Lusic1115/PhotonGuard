'use strict';

const figureSinc = x => x===0 ? 1 : Math.sin(x)/x;
const knowledgeFigures = {
  'fret-distance': {
    title:'距离怎样改变 FRET 效率',kind:'公式教学图',x:'归一化距离 r/R₀',y:'转移效率 E',range:[0,3,0,1],
    note:'固定供体、受体、取向与环境。采用弱耦合点偶极模型。',
    curves:[['E = 1 / (1 + (r/R₀)⁶)',x=>1/(1+x**6)]],ask:[],
  },
  'complex-field-interference': {
    title:'波峰与相位差', kind:'公式图解', x:'位置 kx（rad）', y:'归一场幅', range:[0,4*Math.PI,-1.1,1.1],
    note:'两条同频正弦场相差 π/2。向右移动对应比较沿传播方向的不同位置。',
    curves:[['参考场', x=>Math.cos(x)],['相移场',x=>Math.cos(x-Math.PI/2)]],
    ask:[['phase','同一位置比较两束场时，相位差怎样读？']],
  },
  'phase-group-dispersion': {
    title:'拍频里的快速载波与慢包络', kind:'公式图解', x:'位置（无量纲）', y:'归一场幅',range:[-8,8,-1.1,1.1],
    note:'这是固定时刻的双频叠加。细振荡中的等相位点与外包络的等相位点分别用于讨论相速度和群速度。',
    curves:[['叠加场',x=>Math.cos(5*x)*Math.cos(.27*x)],['包络',x=>Math.cos(.27*x)],['下包络',x=>-Math.cos(.27*x)]],
    ask:[['carrier','群速度和相速度分别对应图上什么？']],
  },
  'fourier-sample-readout': {
    title:'有限观测窗的频谱', kind:'公式图解', x:'普通频率 f（Hz）', y:'归一幅值',range:[-20,20,0,1.04],
    note:'以 1 s 或 2 s 矩形窗为例。横轴 f 采用 Hz；改用 ω 时横坐标乘 2π。',
    curves:[['1 s',f=>Math.abs(figureSinc(Math.PI*f))],
            ['2 s',f=>Math.abs(figureSinc(2*Math.PI*f))]],
    ask:[['leakage','窗长变化为什么改变频谱主瓣宽度？']],
  },
  'polarization-elements': {
    title:'正交场分量构成的偏振椭圆', kind:'公式图解', x:'Eₓ / Aₓ', y:'Eᵧ / Aₓ',range:[-1.1,1.1,-.8,.8],
    note:'等比例坐标。取 Eₓ=cos(ωt)、Eᵧ=0.55 cos(ωt−π/3)。这里展示椭圆形状，旋向还要约定观察方向。',
    equalAxes:true,
    curves:[['电场矢量端点',t=>[Math.cos(t),.55*Math.cos(t-Math.PI/3)],[0,2*Math.PI]]],
    ask:[['representation','两个分量的幅值和相对相位怎样改变椭圆？']],
  },
  'ray-to-gaussian': {
    title:'薄透镜的傍轴光线',kind:'结构示意',x:'位置 z/f',y:'高度 y/f',range:[-1.15,1.15,-.35,.35],
    note:'透镜在 z/f=0。两条平行入射的傍轴光线在透镜处保持高度，斜率发生跳变。',
    curves:[['上方光线',x=>x<0?.18:.18*(1-x)],['下方光线',x=>x<0?-.18:-.18*(1-x)]],
    ask:[['ray','为什么光线高度在透镜处连续，而斜率变化？']],
  },
  'laser-mode-linewidth': {
    title:'增益包络内的多个纵模',kind:'结构示意',x:'相对频率 / FSR',y:'相对响应',range:[-3.5,3.5,0,1.06],
    note:'纵模以 1 FSR 为间隔；峰宽和包络宽度仅用于说明三者的位置关系。',
    curves:[['增益包络',x=>Math.exp(-x*x/4)],
      ['模峰示意',x=>Array.from({length:7},(_,i)=>i-3).reduce((v,k)=>v+.8*Math.exp(-k*k/4-.5*((x-k)/.045)**2),0)]],
    ask:[['width','图中哪两个量分别是 FSR 与单个峰的宽度？']],
  },
  'nonlinear-qpm-from-shg': {
    title:'均匀晶体中的相位失配累积',kind:'公式图解',x:'传播长度 z/L_c',y:'相对生成强度',range:[0,4,0,4.1],
    note:'固定失配并取 L_c=π/abs(Δk)，以匹配时 z=2L_c 的强度作归一参考。小信号平面波，忽略损耗。',
    curves:[['相位匹配',x=>x*x/4],['相位失配',x=>(Math.sin(Math.PI*x/2)/Math.PI)**2]],
    ask:[['integral','为什么传播到 2L_c 时两种情况的结果不同？']],
  },
  'fiber-acceptance-to-mode': {
    title:'基模的横向强度示意',kind:'结构示意',x:'横向位置 / 芯半径',y:'归一强度',range:[-2.5,2.5,0,1.06],
    note:'用高斯包络示意基模尾部可以延伸到包层，虚线标出理想纤芯边界。实际模式需由光纤的波导方程求得。',
    guides:[-1,1],
    curves:[['基模近似',x=>Math.exp(-1.4*x*x)]],
    ask:[['mode','为什么基模的光并非全被限制在纤芯以内？']],
  },
};

function extensionFigure(figure) {
  const x=figure.curves.flatMap(curve=>curve.points.map(point=>point[0]));
  const y=figure.curves.flatMap(curve=>curve.points.map(point=>point[1]));
  const axis=values=>{
    const minimum=Math.min(...values),maximum=Math.max(...values);
    const padding=(maximum-minimum)*.06 || Math.max(Math.abs(maximum)*.1,1);
    return [minimum-padding,maximum+padding];
  };
  return {...figure,kind:'专题图解',range:[...axis(x),...axis(y)],
    curves:figure.curves.map(curve=>[curve.label,null,null,curve.points]),
    ask:[[figure.section,'这张图对应的推导怎样读？']]};
}

function knowledgeFigure(topicId, askSection, extension) {
  const figure = knowledgeFigures[topicId] || (extension && extensionFigure(extension));
  if(!figure)return null;
  const [x0,x1,y0,y1]=figure.range;
  const colors=['#136d80','#b13b4b','#7656a9'];
  const svgNode=(tag,attrs={},value)=>{
    const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
    for(const [key,item] of Object.entries(attrs))node.setAttribute(key,String(item));
    if(value!==undefined)node.textContent=value;
    return node;
  };
  const width=460, height=228, left=56, top=24, plotHeight=153;
  const plotWidth=figure.equalAxes ? plotHeight*(x1-x0)/(y1-y0) : 369;
  const edge=left+plotWidth, bottom=top+plotHeight;
  const px=x=>left+(x-x0)*plotWidth/(x1-x0);
  const py=y=>top+(y1-y)*plotHeight/(y1-y0);
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':figure.title});
  const label=(x,y,value,extra={})=>svgNode('text',{x,y,fill:'#364e55','font-size':12,...extra},value);
  for(let i=0;i<=4;i++){
    const x=x0+(x1-x0)*i/4,y=y0+(y1-y0)*i/4;
    svg.append(svgNode('path',{d:`M${px(x)},${top} V${bottom} M${left},${py(y)} H${edge}`,stroke:'#dbe6e2'}),
      label(px(x),bottom+17,x.toFixed(x1-x0>=15?0:1),{'text-anchor':'middle'}),
      label(left-7,py(y)+4,y.toFixed(1),{'text-anchor':'end'}));
  }
  svg.append(svgNode('path',{d:`M${left},${top} V${bottom} H${edge}`,fill:'none',stroke:'#536972'}),
    label(left,14,figure.y),label((left+edge)/2,bottom+37,figure.x,{'text-anchor':'middle'}));
  for(const x of figure.guides||[])svg.append(svgNode('path',{
    d:`M${px(x)},${top} V${bottom}`,stroke:'#536972','stroke-dasharray':'4 3'}));
  const legend=el('ul',null,'figure-legend');
  for(const [index,[name,fn,domain=[x0,x1],samples]] of figure.curves.entries()){
    const coords=samples || Array.from({length:401},(_,i)=>{
      const v=domain[0]+(domain[1]-domain[0])*i/400;
      const point=fn(v);return Array.isArray(point)?point:[v,point];
    });
    const points=coords.map(([x,y])=>`${px(x).toFixed(2)},${py(y).toFixed(2)}`);
    svg.append(svgNode('polyline',{points:points.join(' '),fill:'none',stroke:colors[index],
      'stroke-width':2,'stroke-dasharray':index===0?'none':index===1?'5 2':'2 2'}));
    legend.append(el('li',name,'figure-curve-'+index));
  }
  const card=el('figure',null,'knowledge-figure');
  card.append(el('h3',figure.title),el('p',figure.kind,'muted'),svg,legend,el('figcaption',figure.note,'muted'));
  for(const [part,question] of figure.ask){
    const button=el('button',question);button.type='button';button.onclick=()=>askSection(part,question);card.append(button);
  }
  return card;
}

function fretComparison(explain){
  const figure=knowledgeFigure('fret-distance');
  const fields=['距离 A','距离 B'].map((name,index)=>{
    const label=el('label',name+'（r/R₀） ');
    const input=el('input');input.type='number';input.step='0.1';input.min='0.1';input.max='3';
    input.value=index?'1.4':'0.9';label.append(input);figure.append(label);return input;
  });
  const result=el('p');result.setAttribute('aria-live','polite');
  const svg=figure.querySelector('svg');
  const points=fields.map(()=>{
    const marker=document.createElementNS(svg.namespaceURI,'circle');
    marker.setAttribute('r','5');marker.setAttribute('fill','#b13b4b');svg.append(marker);return marker;
  });
  const question=el('p',null,'fret-current-question');
  const send=el('button','解释当前比较');send.type='button';
  send.onclick=()=>{if(!send.disabled && explain)explain(question.textContent);};
  function draw(){
    if(fields.some(input=>!input.checkValidity()||!input.value)){
      points.forEach(marker=>marker.setAttribute('visibility','hidden'));
      result.textContent='请填写两个介于 0.1 与 3 的归一化距离。';send.disabled=true;question.textContent='';return;
    }
    const values=fields.map(input=>input.valueAsNumber);
    const fractions=values.map(x=>knowledgeFigures['fret-distance'].curves[0][1](x));
    points.forEach((marker,index)=>{
      marker.setAttribute('visibility','visible');
      marker.setAttribute('cx',String(56+369*values[index]/3));
      marker.setAttribute('cy',String(24+153*(1-fractions[index])));
    });
    const [a,b]=fractions;const signed=value=>(value>=0?'+':'')+value.toFixed(2);
    result.textContent=`程序核算结果　A ${(100*a).toFixed(2)}% · B ${(100*b).toFixed(2)}% · B 相对 A：${signed(100*(b-a))} 个百分点（相对变化 ${signed(100*(b-a)/a)}%）。`;
    question.textContent=`FRET：供体、受体、取向和环境固定，归一化距离 r/R₀ 从 ${values[0]} 改到 ${values[1]}。请用两三句话解释当前效率比较，并区分转移速率与效率。`;
    send.disabled=!explain;
  }
  fields.forEach(input=>input.addEventListener('input',draw));
  figure.append(result,question,send);draw();
  return figure;
}
