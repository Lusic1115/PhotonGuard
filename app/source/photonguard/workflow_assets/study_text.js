'use strict';

window.photonStudyText = (host, value) => {
  const text = String(value ?? '');
  const maths = /\\\(([\s\S]{1,450}?)\\\)|\\\[([\s\S]{1,450}?)\\\]|\$\$([\s\S]{1,450}?)\$\$|(?<!\$)\$([^$\n]{1,280})\$(?!\$)/g;
  const appendText = plain => {
    plain.split('\n').forEach((line,index) => {
      if(index)host.append(document.createElement('br'));
      host.append(document.createTextNode(line));
    });
  };
  let cursor=0;
  for(const match of text.matchAll(maths)){
    appendText(text.slice(cursor,match.index));
    const math=document.createElement('span');
    try{
      window.katex.render(match[1]??match[2]??match[3]??match[4],math,{
        output:'mathml',trust:false,throwOnError:true,maxExpand:60,maxSize:10,
        displayMode:Boolean(match[2]||match[3])
      });
    }catch(_){
      math.textContent=match[0];math.title='公式以原文显示';
    }
    host.append(math);
    cursor=match.index+match[0].length;
  }
  appendText(text.slice(cursor));
  return host;
};
