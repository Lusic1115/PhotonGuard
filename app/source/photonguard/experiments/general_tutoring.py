"""AI explanations for questions with no matching published evidence."""

import re

from ..product_phase import json_payload
from ..workflow_model import wire

RECORD_REQUEST = re.compile(
    r'(?:核对|查询|查看|确认|显示|告诉|查到|查一下|查找).{0,50}'
    r'(?:编号|序列号|设备\s*ID)[\s:：]*[a-z0-9][a-z0-9_-]+', re.I)


def context(question, answer, user_turns):
    previous = {'question':question[:500], 'answer':answer[:400]}
    earlier = [turn[:700] for turn in user_turns[:-1] if turn and not wire.credential_risk(turn)]
    if earlier:
        previous['earlier_user_words'] = earlier[:1] + earlier[-4:] if len(earlier)>5 else earlier
    return previous


def payload(question, previous=None):
    prompt = (
        '你是 PhotonGuard 光学学习助教。这一问没有命中本地依据，只用当前问题给出简短的光学通识讲解。'
        '先用一两句给出答案，再补充必要条件或一个直观例子。简短追问只答追问本身。'
        '用户只问概念、趋势或是否可行时，避免自行添加数值代入、倍数或特定装置参数。'
        '若确实问到了定量关系，明示适用条件，检查计算与结论一致。区分常见实现和成立所需条件。'
        '严格保留用户明确操作的时序。丢弃记录、忘记结果和撤销物理操作不同；'
        '若需要额外物理控制才有另一结论，必须先说明该操作，不能凭“信息被擦除”假定过程被逆转。'
        'previous（如有）是同一学习话题中的上一轮，用于理解新问题所指的对象。以当前问题为准，前文不是已核对的资料。'
        'earlier_user_words 保留用户原话中的数值、单位和假设；优先用最新明确修改。之前的 AI 答复仅用于理解指代。'
        '可给通用公式，注明适用条件。缺少具体设备或实验数据时，只说明需要什么资料，不推测记录、读数或结果。'
        '需要排公式时可用双美元符号包住，JSON 字符串中的反斜线必须写成两个。'
        '问题不属于光学或无法确定指代时，请用户给出具体光学概念。'
        '只输出 JSON 对象 {"intro":"讲解正文"}。不要加入参考链接、虚构文献或声称执行过任何操作。'
    )
    context = {'question': question}
    if re.search(r'\bCARS\b|反斯托克斯拉曼',question+' '+str(previous or ''),re.I):
        prompt += '区分泵浦、斯托克斯和探测，探测可与泵浦共用；时空重叠和相位匹配分别说明。'
    if previous:
        context['previous'] = previous
    return json_payload(prompt, context)


def explanation(obj):
    text = obj.get('intro')
    if (set(obj) != {'intro'} or not isinstance(text, str) or not 8 <= len(text.strip()) <= 900
            or wire.credential_risk(text) or re.search(r'https?[:：]|www\.', text, re.I)):
        raise ValueError('AI 通识讲解格式无效。')
    return text.strip()
