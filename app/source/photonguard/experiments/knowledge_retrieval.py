"""Resolve an optics question against concepts, saved figures and conversation."""

import re
import unicodedata

_FOLLOWUP = re.compile(
    r'没懂|不太懂|不明白|再(?:简单|讲|说|解释)|简单点|这里|那里|刚才|上一(?:个)?问题|上一问|上一版|原来那版|'
    r'上一张图|这(?:条|张图|根|个(?:标记|位置|值))|它(?:怎么|为什么|的)|换(?:个|一种)|例子|类比|用图|'
    r'不要公式|推导|展开|平方|那组参数|总结|规律|打个.{0,5}比方|用通俗的话|沿用')
_TOPIC_SWITCH = re.compile(
    r'^(?:(?:我)?(?:想|要|再|又想)?(?:换(?:个|一)?(?:话题|问题|内容|题)|转到另一题|问个别的)|'
    r'新话题|另外(?:想)?问|顺便问(?:一下|下)?)[，。！？,:：\s]*')
_GENERAL_FOLLOWUP = re.compile(
    r'^(?:那(?:么|如果)?[，,\s]*|如果|所以|这样|这么|这时|此时|它|'
    r'用.{0,10}(?:说法|例子|类比)|(?:别|不带|不用).{0,8}(?:公式|术语))')
_DIRECT_FIGURE = re.compile(r'我点|刚点|这个(?:标记|位置|点|值)|这(?:条|根|张图)|图上|从图.{0,8}向[左右]|位置.{0,8}(?:负号|正号)')
_FIGURE_QUESTION = re.compile(_DIRECT_FIGURE.pattern + r'|这里|那里|它(?:怎么|为什么|的)')
_COMPARE = re.compile(r'对照|两版|两[张幅]图|上一版|前一版|第一版|初版|参考版|原版|原来那版|旧版|两个结果|上次(?:那张|的结果)|改(?:动|完)?前后')
_SCENE_HINTS = {
    'diffraction': re.compile(r'单缝|双缝|衍射|干涉条纹|暗纹'),
    'gaussian': re.compile(r'高斯|束腰|瑞利|TEM.?00', re.I),
    'film': re.compile(r'膜厚|膜层|薄膜|反射率'),
}
_WEAK = frozenset('亮 强度 曲线 纵轴 波长 焦距 透镜 薄透镜 采样 半径 直径 光斑 高斯 薄膜 反射率 双缝 相位 周期 比例'
                  ' 更宽 更窄 减半 测得 不适用 列解释 最细'.split())
_TERM_PREAMBLE = re.compile(r'^(?:那)?(?:请问(?:一下)?|我(?:只)?(?:想知道|问)|麻烦)\s*')
_TERM_PREFIX = re.compile(r'^(?:什么是|什么叫|啥是|啥叫|何谓|(?:请)?(?:用图)?(?:解释|介绍|说明|讲讲|说说)(?:一下|下)?|'
                          r'what\s+is\s+(?:(?:a|an|the)\s+)?|define\s+)\s*')
_TERM_SUFFIX = re.compile(r'\s*(?:这个(?:词|概念))?(?:是(?:什么(?:东西)?|什么意思|啥)|什么意思|指什么|的定义|怎么理解)(?:呀|啊|呢|吗)?$')
_FIGURES = {
    'diffraction': {
        'slit_dark_fringe_left': ('左侧第一暗纹', 'slit-width-definitions', 'first_zero_mm'),
        'slit_dark_fringe_right': ('右侧第一暗纹', 'slit-width-definitions', 'first_zero_mm'),
        'wavelength_nm': ('光源波长', 'slit-scale', 'wavelength_nm'),
        'slit_width_um': ('缝宽', 'aperture-and-fringes', 'slit_width_um'),
        'slit_height_um': ('缝高', 'slit-height', 'slit_height_um'),
        'slit_separation_um': ('中心距', 'double-period', 'slit_separation_um'),
        'focal_length_mm': ('焦距', 'slit-scale', 'focal_length_mm'),
        'focal_plane': ('焦平面', 'focal-plane-model', None),
        'intensity_scale': ('光强显示标尺', 'display-scales', None),
    },
    'gaussian': {
        'waist_radius_um': ('输入束腰', 'gaussian-size', 'waist_radius_um'),
        'focal_length_mm': ('透镜焦距', 'gaussian-lens', 'focal_length_mm'),
        'screen_distance_mm': ('屏幕位置', 'gaussian-waist-screen', 'screen_distance_mm'),
        'new_waist_z_mm': ('新束腰位置', 'gaussian-waist-screen', 'new_waist_z_mm'),
    },
    'film': {
        'incident_index': ('入射介质折射率', 'film-interfaces', 'incident_index'),
        'film_index': ('膜层折射率', 'film-interfaces', 'film_index'),
        'substrate_index': ('基底折射率', 'film-interfaces', 'substrate_index'),
        'thickness_nm': ('膜厚', 'film-ambiguity', 'retained_modes'),
    },
}


def selected_figure(context, figure_id):
    """Look up a real figure control in the verified current result projection."""
    if not context or not isinstance(figure_id, str):
        return None
    domain = context['domain']
    inverse = context['source']['kind'] == 'INVERSE_RESULT'
    if inverse and figure_id == 'fit_residual' and any(
            'training_rmse' in row for row in context['facts']['residual_evidence']):
        item = ('拟合残差', 'fit-residuals', None)
    elif inverse != (domain == 'film'):
        return None
    else:
        item = _FIGURES.get(domain, {}).get(figure_id)
    if not item:
        return None
    label, concept, field = item
    if inverse:
        facts = context['facts']
        values = facts | facts.get('fixed', {})
    else:
        row = next((item for item in context.get('candidates', [])
                    if item['id'] == context.get('selected_candidate_id')), None)
        if not row or not context.get('axes'):
            return None
        if figure_id.startswith('slit_dark_fringe_') and row['parameters'].get('aperture') != 'single_slit':
            return None
        values = row['parameters'] | row['metrics']
    if field and values.get(field) is None:
        return None
    return {'id': figure_id, 'label': label, 'concept_id': concept, 'source': context['source'],
            'candidate_id': context.get('selected_candidate_id')}


def question_kind(question, depth='short'):
    if _COMPARE.search(question):
        return 'COMPARE'
    if re.search(r'验证|怎么试|观察办法|怎么确认|如何核对|实际试', question):
        return 'VERIFY'
    if re.search(r'适用|假设|前提|误区|理解对|我以为|错在哪|可信|能信|保证', question):
        return 'CHECK'
    if depth == 'formula' or re.search(r'推导|公式|平方|怎么算|怎么来的|为什么.{0,12}(?:四倍|加倍)', question):
        return 'DERIVE'
    if _FIGURE_QUESTION.search(question) or re.search(r'读图|纵轴|色标|标尺', question):
        return 'READ'
    return 'EXPLAIN'


def is_followup(question):
    return not switches_topic(question) and bool(
        _FOLLOWUP.search(question) or _GENERAL_FOLLOWUP.match(question.strip()))


def switches_topic(question):
    return bool(_TOPIC_SWITCH.match(question.strip()) or
                re.match(r'^(?:不要|不再|先不|别再)(?:讲|聊|讨论|继续|问).{1,18}(?:问题|话题|主题)(?:了)?[。；;，,]',question.strip()))


def canonical_notation(question):
    """Normalize equivalent typed ket delimiters, without changing user facts."""
    text=unicodedata.normalize('NFKC',question)
    return re.sub(r'[|∣]\s*([01+−-])\s*[>⟩〉]',lambda m:'|'+m[1].replace('−','-')+'⟩',text)


def general_followup(question):
    return not requested_definition(question) and (is_followup(question) or
        not switches_topic(question) and bool(_GENERAL_FOLLOWUP.match(question.strip())))


def adds_unasked_scene(text, question, domain):
    return any(key != domain and pattern.search(text) and not pattern.search(question)
               for key, pattern in _SCENE_HINTS.items())


def gaussian_screen_waist_question(question):
    return bool(re.search(r'屏幕|屏的位置', question) and re.search(r'束腰|最细处', question)
                and re.search(r'移|挪|改|换位置|向前|向后|往前|往后|跟着|会变', question))


def result_concepts(question, context, view):
    if not context or context['source']['kind'] != 'FORWARD_RESULT':
        return ()
    if context['domain'] == 'gaussian' and gaussian_screen_waist_question(question):
        return ('gaussian-waist-screen',)
    if context['domain'] != 'diffraction':
        return ()
    if any(pattern.search(question) for name, pattern in _SCENE_HINTS.items() if name != 'diffraction'):
        return ()
    if view == 'peak' and re.search(r'最高点|峰值|峰高|归一化|纵轴|标尺', question) and re.search(r'亮|相同|一样|比较|可比', question):
        return ('display-scales',)
    selected = next((row for row in context['candidates'] if row['id'] == context.get('selected_candidate_id')), {})
    if (selected.get('parameters', {}).get('aperture') == 'double_slit'
            and re.search(r'相位差|相对相位|同相|反相', question)
            and re.search(r'中心|间距|周期|变密|变稀', question)):
        return ('double-phase',)
    return ()


def _rank(cards, question, domain, hints):
    query = question.casefold()
    ranked = []
    for card in cards:
        if card['id'] == 'film-reflectance-input' and 'film' not in (hints or {domain}):
            continue
        matches = sorted({alias.casefold() for alias in card['aliases'] if alias.casefold() in query},
                         key=lambda word: (-len(word), word))
        strong = [word for word in matches if word not in _WEAK]
        distinct = []
        for word in strong:
            if not any(word in previous for previous in distinct):
                distinct.append(word)
        if distinct:
            score = 6 + min(len(distinct[0]), 12) + min(5, 2 * (len(distinct) - 1))
        elif (len(matches) >= 2 or domain in card['scope'] and re.search(r'为什么|怎么|什么|变|更|区别', question)) and matches:
            score = min(5, len(matches) + 1)
        else:
            continue
        score += 3 * bool(hints.intersection(card['scope'])) + int(domain in card['scope'])
        ranked.append((score, bool(distinct), card))
    return sorted(ranked, key=lambda row: -row[0])


def _term_key(text):
    return ''.join(unicodedata.normalize('NFKC', text).casefold().split()).strip('!?。:「」“”"')


def definition_term(question):
    query = _TOPIC_SWITCH.sub('', unicodedata.normalize('NFKC', question).casefold().strip(' ?!。:'))
    query = _TERM_PREAMBLE.sub('', query)
    return _TERM_SUFFIX.sub('', _TERM_PREFIX.sub('', query)).strip(' :「」“”"')


def requested_definition(question):
    """Read an explicitly named definition, including one followed by a second question."""
    first = re.split(r'[？?。！!]|[，,](?:不谈|不聊|先不|别再|不要)', question, maxsplit=1)[0].strip()
    query = _TERM_PREAMBLE.sub('', _TOPIC_SWITCH.sub('', first))
    if not (_TERM_PREFIX.match(query) or _TERM_SUFFIX.search(query)):
        return ''
    term = definition_term(re.sub(r'[呀啊呢呗嘛]$', '', query))
    return re.sub(r'^(?:在)?(?:光学|非线性光学)(?:中|里)的?', '', term).strip()


def _definition_card(catalog, question):
    term = _term_key(definition_term(question))
    matches = [card for card in catalog if any(_term_key(name) == term for name in card.get('terms', []))]
    return matches[0] if len(matches) == 1 else None


def retrieve(catalog, question, domain=None, *, topic=None, figure=None, kind='EXPLAIN', anchored=()):
    by_id = {card['id']: card for card in catalog}
    definition = None if anchored else _definition_card(catalog, question)
    named_scenes = {key for key, pattern in _SCENE_HINTS.items() if pattern.search(question)}
    ranked = _rank(catalog, question, domain, named_scenes)
    cards = []
    basis = 'CLARIFY'
    if len(ranked) > 1 and not (anchored or definition or domain or figure or named_scenes) and ranked[0][:2] == ranked[1][:2]:
        first, second = ranked[0][2], ranked[1][2]
        if not set(first['scope']).intersection(second['scope']):
            previous = by_id.get(topic['id']) if topic and is_followup(question) else None
            if not previous:
                return [], basis
            matching = [row for row in ranked if row[:2] == ranked[0][:2] and
                        set(previous['scope']).intersection(row[2]['scope'])]
            if len(matching) != 1:
                return [], basis
            ranked.remove(matching[0])
            ranked.insert(0, matching[0])
    explicit = [card for _, strong, card in ranked if strong]
    if anchored:
        cards = [by_id[key] for key in anchored]
        basis = 'RESULT'
    elif definition:
        cards = [definition]
        basis = 'DEFINITION'
    elif figure and _FIGURE_QUESTION.search(question) and (
            not topic or topic['id'] == figure['concept_id'] or _DIRECT_FIGURE.search(question)) and (
            not explicit or explicit[0]['id'] == figure['concept_id']):
        cards = [by_id[figure['concept_id']]]
        basis = 'FIGURE'
    elif explicit:
        scope = set(explicit[0]['scope'])
        cards = [card for card in explicit if set(card['scope']) & (scope | named_scenes)][:3]
        basis = 'QUESTION'
    elif topic and is_followup(question) and topic['id'] in by_id:
        cards = [by_id[topic['id']]]
        basis = 'CONVERSATION'
    elif ranked and not _FIGURE_QUESTION.search(question):
        cards = [ranked[0][2]]
        basis = 'QUESTION'
    if not cards:
        return cards, basis
    primary = cards[0]
    if basis not in ('QUESTION', 'DEFINITION'):
        cards += [card for card in explicit if card not in cards and set(card['scope']) & set(primary['scope'])][:3 - len(cards)]
    if len(cards) < 3 and kind in ('DERIVE', 'CHECK', 'VERIFY', 'COMPARE'):
        key = next((key for key in primary.get('prerequisite_ids', []) if by_id[key] not in cards), None)
        if key:
            cards.append(by_id[key])
    return cards, basis
