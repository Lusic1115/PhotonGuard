"""Resolve a follow-up against quoted user turns before retrieving evidence."""
import re
import unicodedata
from uuid import uuid4
from ..product_phase import json_payload
from ..workflow import WorkflowError
from ..workflow_model import wire
from . import tutor


def _numbers(text):
    return set(re.findall(r'\d+(?:\.\d+)?',unicodedata.normalize('NFKC',text)))


def validate(value, turns):
    if not isinstance(value,dict) or set(value)!={'resolved_question','evidence'}:
        raise ValueError('Context format')
    question=value['resolved_question'];evidence=value['evidence']
    if not isinstance(question,str) or not 8<=len(question)<=1000 or wire.credential_risk(question):
        raise ValueError('Context question')
    if not isinstance(evidence,list) or not 1<=len(evidence)<=12:
        raise ValueError('Context evidence')
    seen=set()
    for item in evidence:
        if not isinstance(item,dict) or set(item)!={'turn','quote'}:
            raise ValueError('Context citation')
        number,quote=item['turn'],item['quote']
        if type(number) is not int or not 1<=number<=len(turns) or not isinstance(quote,str) or not quote.strip() or quote not in turns[number-1]:
            raise ValueError('Context quote differs from user words')
        seen.add(number)
    if len(turns) not in seen:
        raise ValueError('Context omitted current question')
    if _numbers(question)-_numbers(' '.join(turns)):
        raise ValueError('Context introduced a number')
    return question


def resolve(client, question, history, capture):
    turns=[turn['question'] for turn in history[-6:]]+[question]
    prompt=('你只整理连续提问，不回答光学问题，也不计算。把最后一句改写成包含当前有效条件的完整问题。'
            '只以编号user_turns的用户原话为依据，不添加示例条件或推测的数字。'
            '最新明确修改替换同一条件，其他已知条件继续保留；上一轮的“如果”是刚讨论的分支，'
            '下一句“其他不变”沿用该分支，不擅自回到第一轮。'
            '只还原最后一句当前要问的问题。历史只作为必要条件，不罗列已经问过的问题和计算请求。'
            '已撤销的假设不再写成当前条件；新旧条件冲突时只保留最新明确修改。'
            '区分接着执行下一操作与取消前操作重新开始，完整保留先后顺序和是否保留测量结果。'
            '只要求简短、去公式或比较时，完整问题仍是上一轮的实际对象和更新条件，不改成概念定义。'
            '“不要讨论/别做”的旁支不能成为问题主题。新命名主题优先于旧对象。'
            '不用解答里的推导或计算值填条件，不能将目标值改写成已测结果。'
            '输出JSON，恰有resolved_question和evidence两个字段。evidence为turn与quote组成的数组，'
            'quote必须逐字摘自对应user_turns，包含当前句及每项沿用/更正条件的原话。'
            'resolved_question限1000字，所有数字保留用户原写法，不换算单位，不添加答案。')
    payload=json_payload(prompt,{'user_turns':[{'turn':i,'text':t} for i,t in enumerate(turns,1)]})
    audit={'attempted':True,'accepted':False}
    capture['context_resolution']=audit
    try:
        raw=client.complete('knowledge-context-'+uuid4().hex,payload)
        audit['assistant']=raw.get('assistant')
        audit['transport']={k:raw.get('audit',{}).get(k) for k in ('status','live_dispatch','provider_model','request_sha256','response_sha256')}
        value=tutor.strict_object(raw);resolved=validate(value,turns)
        audit.update(accepted=True,resolved_question=resolved,evidence=value['evidence'])
        return resolved
    except (WorkflowError,ValueError,KeyError,TypeError,RuntimeError):
        audit['message']='Context resolution was not accepted; retained the original question.'
        return None
