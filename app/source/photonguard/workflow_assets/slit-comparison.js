"use strict";

// Presentation of two verified, saved FFT arrays. Display changes never alter results.
window.photonSlitComparison = ({node, append, button, plot}, previous, current, summary) => {
  const FULL = 'central_zero_to_zero_width_mm';
  const rows = [previous,current];
  const widths = rows.map(row=>2*row.metrics.first_zero_mm);
  if (rows.some(row=>row.parameters.aperture!=='single_slit' || !row.all_checks_passed || !row.data?.image?.values?.length)
      || widths.some(value=>!Number.isFinite(value))) return null;
  const fmt = value=>Number(value.toPrecision(6)).toString();
  const svgNS='http://www.w3.org/2000/svg';
  const svgNode=(tag,attributes={},text)=>{
    const item=document.createElementNS(svgNS,tag);
    for(const [key,value] of Object.entries(attributes))item.setAttribute(key,String(value));
    if(text!=null)item.textContent=text;
    return item;
  };
  const images=rows.map(row=>row.data.image);
  const extent=axis=>[axis[0]-(axis[1]-axis[0])/2,axis.at(-1)+(axis.at(-1)-axis.at(-2))/2];
  const extents=images.map(im=>({x:extent(im.x),y:extent(im.y)}));
  const supportedX=Math.min(...extents.flatMap(e=>[-e.x[0],e.x[1]]),...rows.map(r=>r.parameters.focal_length_mm*.1));
  const supportedY=Math.min(...extents.flatMap(e=>[-e.y[0],e.y[1]]),...rows.map(r=>r.parameters.focal_length_mm*.1));
  const limitX=Math.min(supportedX,Math.max(...widths)*.9);
  const verticalWidths=rows.map(r=>2*r.parameters.wavelength_nm*1e-6*r.parameters.focal_length_mm/(r.parameters.slit_height_um*.001));
  const limitY=Math.min(supportedY,Math.max(...verticalWidths)*1.3);
  if(!(limitX>0 && limitY>0))return null;
  const maxOf=values=>values.reduce((a,b)=>Number.isFinite(b)?Math.max(a,b):a,0);
  const peaks=images.map(im=>maxOf(im.values.map(maxOf)));
  const commonMax=Math.max(...peaks);
  const view=node('section',null,'slit-compare-view');view.id='slit-result-comparison';
  const big=node('div',null,'slit-compare-kpis');
  rows.forEach((row,i)=>big.append(append(node('div'),node('span',i?'本版 · 解析全宽':'来源 · 解析全宽'),
    node('strong',fmt(widths[i])+' mm'),node('span','缝宽 '+fmt(row.parameters.slit_width_um)+' μm'))));
  const fixedKeys={wavelength_nm:['波长','nm'],focal_length_mm:['焦距','mm'],slit_height_um:['缝高','μm']};
  const fixed=Object.entries(fixedKeys).filter(([key])=>Object.hasOwn(summary.retained,key)).map(([key,[label,unit]])=>label+' '+fmt(summary.retained[key])+' '+unit);
  const mode=node('select');mode.setAttribute('aria-label','两版共同显示方式');
  for(const [value,label] of [['peak','各自峰值归一化 · 比较形状'],['physical','共同入射光强 · 比较强度']]){
    const item=node('option',label);item.value=value;mode.append(item);
  }
  const explanation=node('p',null,'slit-compare-caption');
  const gallery=node('div',null,'slit-compare-images'),curve=node('div',null,'slit-compare-curve');
  function heatmap(index,normalizer){
    const im=images[index],raw=document.createElement('canvas');
    raw.width=im.values[0].length;raw.height=im.values.length;
    const context=raw.getContext('2d'),pixels=context.createImageData(raw.width,raw.height);
    const stops=[[8,15,40],[40,73,141],[16,161,164],[253,224,71]];
    for(let y=0;y<raw.height;y++)for(let x=0;x<raw.width;x++){
      const t=Math.max(0,Math.min(1,im.values[y][x]/(normalizer||1))),pos=t*3,k=Math.min(2,Math.floor(pos)),f=pos-k;
      const offset=((raw.height-1-y)*raw.width+x)*4;
      for(let c=0;c<3;c++)pixels.data[offset+c]=Math.round(stops[k][c]*(1-f)+stops[k+1][c]*f);
      pixels.data[offset+3]=255;
    }
    context.putImageData(pixels,0,0);
    const cropped=document.createElement('canvas');cropped.width=900;cropped.height=400;
    const e=extents[index],sx=(-limitX-e.x[0])/(e.x[1]-e.x[0])*raw.width,sy=(e.y[1]-limitY)/(e.y[1]-e.y[0])*raw.height;
    const ctx=cropped.getContext('2d');ctx.imageSmoothingEnabled=false;
    ctx.drawImage(raw,sx,sy,2*limitX/(e.x[1]-e.x[0])*raw.width,2*limitY/(e.y[1]-e.y[0])*raw.height,0,0,900,400);
    const svg=svgNode('svg',{viewBox:'0 0 500 245',role:'img','aria-label':(index?'本版':'来源')+'实际 FFT 二维光强，同一坐标范围'});
    svg.append(svgNode('image',{href:cropped.toDataURL(),x:50,y:15,width:435,height:180,preserveAspectRatio:'none'}));
    for(const v of [-1,0,1]){
      svg.append(svgNode('text',{x:50+(v+1)*217.5,y:213,'text-anchor':'middle',fill:'#183f3a','font-size':14},fmt(v*limitX)));
      svg.append(svgNode('text',{x:43,y:109-v*90,'text-anchor':'end',fill:'#183f3a','font-size':14},fmt(v*limitY)));
    }
    svg.append(svgNode('text',{x:270,y:236,'text-anchor':'middle',fill:'#183f3a','font-size':14},'焦平面 x（mm）'));
    svg.append(svgNode('text',{x:13,y:105,transform:'rotate(-90 13 105)','text-anchor':'middle',fill:'#183f3a','font-size':14},'y（mm）'));
    const legend=append(node('div',null,'slit-compare-colorbar'),node('span','0'),node('i'),node('span',mode.value==='peak'?'1':fmt(commonMax)));
    return append(node('figure'),node('figcaption',(index?'本版':'来源')+' · 实际 FFT 光强'),svg,legend);
  }
  function draw(){
    const normalized=mode.value==='peak',scale=normalized?peaks:[commonMax,commonMax];
    gallery.replaceChildren(...rows.map((row,i)=>heatmap(i,scale[i])));
    const series=rows.map((row,i)=>{
      const source=row.data.series[0],peak=maxOf(source.y);
      const selected=row.data.x.map((x,j)=>[x,source.y[j]]).filter(([x])=>Math.abs(x)<=limitX);
      return {label:(i?'本版':'来源')+' · '+fmt(widths[i])+' mm',x:selected.map(p=>p[0]),y:selected.map(p=>normalized?p[1]/peak:p[1])};
    });
    const marks=widths.flatMap((w,i)=>[-1,1].map(sign=>({x:sign*w/2,label:(i?'本版':'来源')+'解析第一暗纹 '+fmt(sign*w/2)+' mm'})));
    curve.replaceChildren(plot({comparison_view:true,x:series.flatMap(s=>s.x),series,x_label:'焦平面 x（mm）',y_label:normalized?'光强 / 各自峰值':'光强 / 入射光强'},
      {x:[-limitX,limitX],y:[0,normalized?1.04:commonMax*1.04]},marks));
    curve.querySelectorAll('.field-note').forEach(item=>item.remove());
    curve.append(node('p','虚线标记解析第一暗纹；曲线连接实际 FFT 采样点。','field-note'));
    explanation.textContent=normalized?'两图与曲线各按自身峰值归一化，色标 0–1，用于比较形状。':'两图共用色标 0–'+fmt(commonMax)+'，入射光强为 1。';
    view.dataset.displayMode=mode.value;view.dataset.xRange=[-limitX,limitX].join(',');view.dataset.yRange=[-limitY,limitY].join(',');
  }
  mode.onchange=draw;
  const actions=append(node('div',null,'actions'),mode);
  const focused=button('专注查看',()=>{
    view.classList.toggle('slit-compare-focus');focused.textContent=view.classList.contains('slit-compare-focus')?'返回实验':'专注查看';
    focused.focus();
  },'small');actions.append(focused);
  view.addEventListener('keydown',event=>{if(event.key==='Escape'){view.classList.remove('slit-compare-focus');focused.textContent='专注查看';}});
  const details=node('details');details.append(node('summary','查看两版依据与实际后端'),node('p',rows.map((r,i)=>(i?'本版':'来源')+'：'+r.engine.name).join('；')),
    node('pre',JSON.stringify({prior:summary.prior,current:summary.current,changed:summary.changed,retained:summary.retained},null,2)));
  const ratio=widths[1]/widths[0];
  const labels={...fixedKeys,slit_width_um:['缝宽','μm']};
  const changed=Object.entries(summary.changed).filter(([key])=>labels[key]).map(([key,[a,b]])=>labels[key][0]+' '+fmt(a)+' → '+fmt(b)+' '+labels[key][1]);
  append(view,node('h3','两版结果比较'),big,node('p','改变 · '+changed.join('；')+'　保持 · '+fixed.join(' · ')),
    actions,gallery,curve,explanation,
    node('p','解析全宽为来源的 '+fmt(ratio)+' 倍。光源和透镜相同时，全宽与缝宽成反比。','slit-compare-takeaway'),details);
  draw();return view;
};
