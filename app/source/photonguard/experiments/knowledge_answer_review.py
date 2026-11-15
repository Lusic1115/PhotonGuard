"""Check a draft explanation against user conditions and cited article text."""
from uuid import uuid4
from ..product_phase import json_payload
from ..workflow import WorkflowError
from . import tutor


def review(client, result, question, history, capture):
    general=result['mode']=='LIVE_AI_GENERAL'
    prompt=('你是光学助教的答复核对环节。给定用户当前问题、此前用户原话、补全语境、程序结果、资料正文和初稿。'
            '直接输出修正后的最终答复，不输出审查过程。先核对当前有效条件，修正首句与计算、排序、单位、结尾的矛盾。'
            '比较数值时实际比较大小；有条件更正时只用最新条件；顺序执行与重新制备要区分。'
            '检查数量级与单位换算，W与W²、GHz与THz、百分点与相对百分比不能混用。'
            '程序登记结果优先；来源示例不是用户参数。条件不足时只回答可推出的结论。'
            '保留正确的直接答案，删除初稿中未问的延伸与重复定义。用户要求一句话就一句，两句话就两句。'
            '未要求详细推导时，只给结论、关键数字和必要条件，不复述初稿的整段计算过程。'
            '需推导时逐个核对十进制指数与单位：先以SI单位计算，再转换到展示单位，不能只改单位标签。'
            '每个引用必须在所给正文中实际支持该段，不把编号合法当作结论有据。'
            '不要添加原稿和资料之外的新实验成绩、来源或链接，不声称进行了仿真。')
    prompt+=('没有本地依据时给通识解释，输出JSON对象{"intro":"正文"}。' if general else
             '通常输出JSON对象，唯一字段sections。每段恰有heading、text、references，引用只用articles中的id。一般一到两段；用户要推导时可增加。'
             '若articles仅在词语上相似、并无支持本题结论的内容，改用JSON对象{"intro":"简短通识讲解"}；不要强贴无关引用。')
    used=set(result.get('evidence_ids',[]))
    used.update(i for section in result.get('sections',[]) for i in section.get('references',[]))
    articles=[{k:a.get(k) for k in ('id','title','definition','body','formula','conditions','caution')}
              for a in result.get('references',[]) if a['id'] in used]
    content={'question':question,'recent_user_turns':capture.get('active_user_turns',[]),
             'resolved_context':capture.get('context_resolution',{}).get('resolved_question'),
             'calculation':result.get('calculation'),'articles':articles,'draft':result['sections']}
    audit={'attempted':True,'accepted':False};capture['answer_review']=audit
    try:
        raw=client.complete('knowledge-review-'+uuid4().hex,json_payload(prompt,content))
        audit['assistant']=raw.get('assistant')
        audit['transport']={k:raw.get('audit',{}).get(k) for k in ('status','live_dispatch','provider_model','request_sha256','response_sha256')}
        return raw
    except (WorkflowError,ValueError,KeyError,TypeError,RuntimeError):
        audit['message']='Review unavailable; preserved the draft and its evidence.'
        return None
