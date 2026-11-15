"""Versioned, offline optics articles and the local full-text index."""

import json
import math
from collections import Counter
from functools import lru_cache
from graphlib import CycleError, TopologicalSorter
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import re
import sqlite3
from threading import Lock
import unicodedata
from urllib.parse import urlsplit

from ..core import digest
from .knowledge_conventions import CONVENTIONS
from .knowledge_retrieval import definition_term, requested_definition, _WEAK

DATA = files(__package__).joinpath('knowledge_data')
IDENTIFIER = re.compile(r'[a-z][a-z0-9-]{1,79}\Z')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+\Z')
TOPICS = {'foundations':'基础与电磁波','imaging':'几何光学与成像','wave':'干涉、衍射与偏振',
          'fourier':'傅里叶光学与成像分析','laser':'激光、谐振腔与光束','guided':'光纤、波导与集成光学',
          'nonlinear':'非线性光学','quantum':'量子光学','measurement':'探测、测量与数值方法',
          'math':'光学数学与统计','ultrafast':'脉冲、色散与超快光学'}
LEVELS = {'intro':'入门','undergraduate':'本科','advanced':'进阶'}
LICENSES = {'GPL-2.0-or-later':'项目原创内容（GPL-2.0-or-later）',
            'CC-BY-4.0':'CC BY 4.0','CC-BY-SA-4.0':'CC BY-SA 4.0'}
PACK_TITLES = {'rc15-cards':'实验知识卡','optics-core':'光学基础',
               'fiber-integrated-optics':'光纤与集成光学','optical-measurement':'探测与测量',
               'quantum-foundations':'量子基础','geometrical-imaging':'几何光学与成像',
               'fourier-imaging':'傅里叶光学与成像','laser-resonators':'激光与谐振腔',
               'polarization-coherence':'偏振与相干','optical-mathematics':'光学数学与统计',
               'electromagnetic-foundations':'电磁场与介质界面','nonlinear-foundations':'非线性极化与频率转换',
               'ultrafast-pulse-learning':'脉冲、色散与超快光学','nonlinear-coupled-waves':'非线性耦合波与参量过程',
               'guided-photonics-connections':'波导与集成光学连接'}
CAPABILITIES = {
    'slit-scale':('单缝衍射','#library=D01'),
    'slit-width-definitions':('单缝衍射','#library=D01'),
    'diffraction-basics':('单缝衍射','#library=D01'),
    'double-period':('双缝干涉','#library=D02'),
    'double-phase':('双缝干涉','#library=D03'),
    'gaussian-waist':('高斯光束传播','#library=G01'),
    'gaussian-waist-screen':('高斯光束传播','#library=G02'),
    'gaussian-lens':('高斯光束传播','#library=G01'),
    'quarter-wave':('薄膜正向计算','#library=F02'),
    'film-ambiguity':('膜厚反向分析','#inverse-import'),
}
PATHS = {
    'fourier-filtering': ('理解空间滤波', ('spatial-frequency','fourier-plane','abbe-imaging','spatial-filtering')),
    'laser-cavity': ('入门激光谐振腔', ('laser-basics','optical-resonator','fabry-perot','free-spectral-range','laser-longitudinal-modes')),
    'fiber-guidance': ('从接受角到光纤模式', ('optics-total-internal-reflection','fiber-acceptance-angle','fiber-numerical-aperture','fiber-v-number')),
    'nonlinear-matching': ('从倍频到准相位匹配', ('second-harmonic','phase-mismatch','coherence-length-shg','periodic-poling','quasi-phase-matching')),
    'polarization-elements': ('认识偏振元件', ('polarization','linear-polarizer','waveplate','half-waveplate','quarter-waveplate')),
    'fiber-link-measurement': ('从光纤损耗到 OTDR', ('fiber-attenuation-coefficient','fiber-cutback','otdr','otdr-bidirectional')),
    'silicon-ring-spectrum': ('读懂微环谐振谱', ('microring-resonator','microring-fsr','microring-critical-coupling','microring-loaded-q')),
    'photodetection-noise': ('理解光电探测噪声', ('photodiode-responsivity','photodiode-shot-noise','detector-nep','detector-noise-bandwidth')),
    'measurement-report': ('给结果附上不确定度', ('measurement-measurand','measurement-type-a','measurement-type-b','measurement-combined-uncertainty','measurement-expanded-uncertainty')),
    'quantum-state-and-photons': ('从量子态到单光子干涉', ('quantum-superposition','quantum-number-state','quantum-beam-splitter','quantum-hom-interference')),
}
PATH_LESSONS = {
    'fourier-filtering': 'coherence-and-imaging',
    'laser-cavity': 'laser-mode-linewidth',
    'fiber-guidance': 'fiber-acceptance-to-mode',
    'nonlinear-matching': 'nonlinear-qpm-from-shg',
    'polarization-elements': 'polarization-elements',
    'fiber-link-measurement': 'otdr-distance-and-loss',
    'silicon-ring-spectrum': 'ring-fsr-coupling',
    'photodetection-noise': 'nep-bandwidth-detectivity',
    'measurement-report': 'uncertainty-evaluation',
    'quantum-state-and-photons': 'quantum-state-measurement',
}


def normalize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return re.sub(r'[‐‑–—−]', '-', text).strip()


def lookup_term(question):
    value = normalize(question)
    return requested_definition(value) or definition_term(value)


_STOP = set('什么 为什么 怎么 怎样 哪些 哪个 是否 是不是 解释 理解 区别 有什么 一下 如何 可以 关于 问题 原因 我想 帮我 '
            '请问 的话 这个 那个 在哪 什么是 是什么 一个 一种 关系 他们 它们 这里 那里 知道 继续 已经 因为 之间 测得 '
            'what is the a an of to how why difference between and in for photonguard please explain tell me about simple terms'.split())


def tokens(text):
    words = []
    for run in re.findall(r'[\u3400-\u9fff]+|[^\W\d_\u3400-\u9fff]+[0-9]*|[0-9]+', normalize(text)):
        if '\u3400' <= run[0] <= '\u9fff' and len(run) > 1:
            words.extend(run[i:i+2] for i in range(len(run)-1) if run[i:i+2] not in _STOP)
        elif run not in _STOP:
            words.append(run)
    return words


def _read(item):
    return json.loads(item.read_text(encoding='utf-8'))


def _valid_link(link):
    url = urlsplit(link)
    return url.scheme == 'https' and url.hostname and not url.username and not url.password


def term_matches(term, text):
    if term == text.strip(' ?。!？'):
        return True
    if len(term) < 2:
        return False
    if re.search(r'[\u3400-\u9fff]', term):
        return term in text
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text))


def validate_pack(pack):
    required_pack={'id','version','sources','entries'}
    if not isinstance(pack, dict) or not required_pack <= pack.keys() or pack.keys()-required_pack-{'topics'}:
        raise ValueError('知识包需要 id、version、sources 和 entries。')
    if (not isinstance(pack['id'],str) or not IDENTIFIER.fullmatch(pack['id'])
            or not isinstance(pack['version'],str) or not VERSION.fullmatch(pack['version'])):
        raise ValueError('知识包编号或版本格式有误。')
    if not isinstance(pack['sources'], dict) or not isinstance(pack['entries'], list) or not pack['entries']:
        raise ValueError('知识包缺少来源或内容。')
    for key, source in pack['sources'].items():
        required = ('title','url','author','edition','section','accessed','license','rights','fingerprint','excluded_material')
        if (not IDENTIFIER.fullmatch(key) or not isinstance(source,dict)
            or any(not isinstance(source.get(f),str) or not source[f] for f in required)):
            raise ValueError(f'来源缺少版本、定位、权利或指纹记录 {key}')
        if (not _valid_link(source['url']) or source['rights'] not in ('REFERENCE_ONLY','LICENSED_ADAPTATION')
            or not re.fullmatch(r'sha256:[0-9a-f]{64}',source['fingerprint'])):
            raise ValueError(f'来源地址或使用方式无效 {key}')
    ids, titles = set(), set()
    for entry in pack['entries']:
        if (not isinstance(entry,dict)
            or any(not isinstance(entry.get(k),str) or not entry[k].strip()
                   for k in ('id','title','topic','level','license','origin','status'))
            or any(not isinstance(entry.get(k),list) or any(not isinstance(v,str) or not v.strip() for v in entry[k])
                   for k in ('terms','sources','conditions','prerequisites','related'))
            or any(entry.get(k) is not None and not isinstance(entry[k],str) for k in ('formula','variables'))):
            raise ValueError('条目字段类型或必填值无效。')
        ident, title = entry['id'], normalize(entry['title'])
        if not IDENTIFIER.fullmatch(ident) or ident in ids or title in titles:
            raise ValueError(f'条目重复或编号无效 {ident}')
        ids.add(ident);titles.add(title)
        if entry['topic'] not in TOPICS or entry['level'] not in LEVELS or entry['license'] not in LICENSES:
            raise ValueError(f'条目的主题、层级或许可无效 {ident}')
        if entry['status'] not in ('PUBLISHED','DRAFT') or entry['origin'] not in ('ORIGINAL','ADAPTED'):
            raise ValueError(f'发布状态或内容来源无效 {ident}')
        refs = entry['sources']
        if not refs or any(ref not in pack['sources'] for ref in refs):
            raise ValueError(f'缺少可定位来源 {ident}')
        claims = entry.get('claims', [])
        if not isinstance(claims, list) or len(claims) > 3:
            raise ValueError(f'核心依据格式无效 {ident}')
        for claim in claims:
            if (not isinstance(claim, dict) or set(claim) != {'kind','statement','sources'}
                or claim['kind'] not in ('DIRECT','DERIVED','WORKED_EXAMPLE','UNVERIFIED')
                or not isinstance(claim['statement'], str) or not claim['statement'].strip()
                or not isinstance(claim['sources'], list)
                or any(source not in refs for source in claim['sources'])
                or (claim['kind'] in ('DIRECT','DERIVED') and not claim['sources'])):
                raise ValueError(f'核心结论缺少有效依据 {ident}')
        if entry['origin']=='ADAPTED' and any(pack['sources'][r]['rights']!='LICENSED_ADAPTATION'
                                             or pack['sources'][r]['license']!=entry['license'] for r in refs):
            raise ValueError(f'改编内容需带相容的许可和署名 {ident}')
        if entry['origin']=='ADAPTED' and not entry.get('adaptation_note'):
            raise ValueError(f'改编条目缺少署名或改动说明 {ident}')
        if entry['status']=='PUBLISHED':
            if (not all(isinstance(entry.get(k),str) and entry[k].strip() for k in ('definition','body','example','caution'))
                or len(entry['body'])<55 or not entry.get('conditions') or not entry.get('review')):
                raise ValueError(f'正文或发布校对记录不足 {ident}')
            if entry.get('formula') and not entry.get('variables'):
                raise ValueError(f'公式缺少变量和单位约定 {ident}')
    topics=pack.get('topics',[])
    if not isinstance(topics,list) or len(topics)>32:
        raise ValueError('专题必须为列表。')
    for topic in topics:
        keys={'id','title','intro','prerequisites','sections'}
        if (not isinstance(topic,dict) or not keys<=topic.keys() or topic.keys()-keys-{'figure'}
                or any(not isinstance(topic[key],str) or not topic[key].strip() for key in ('id','title','intro'))
                or not isinstance(topic['prerequisites'],list)
                or any(not isinstance(ref,str) for ref in topic['prerequisites'])
                or not isinstance(topic['sections'],list) or not 3<=len(topic['sections'])<=20):
            raise ValueError('专题需要标题、导语、前置知识与至少三节正文。')
        for part in topic['sections']:
            if (not isinstance(part,dict) or set(part)!={'id','title','text','formula','conditions','references'}
                    or any(not isinstance(part[key],str) for key in ('id','title','text','formula','conditions'))
                    or not part['title'].strip() or not isinstance(part['references'],list)
                    or any(not isinstance(ref,str) for ref in part['references'])):
                raise ValueError('专题小节需要标题、正文、条件和引用条目。')
        if 'figure' in topic:
            _validate_figure(topic['figure'],{part['id'] for part in topic['sections']})
    return pack


def _validate_figure(figure, sections):
    keys={'title','x','y','note','curves','section'}
    if (not isinstance(figure,dict) or set(figure)!=keys
            or any(not isinstance(figure[k],str) or not 1<=len(figure[k])<=300
                   for k in keys-{'curves'})
            or figure['section'] not in sections
            or not isinstance(figure['curves'],list) or not 1<=len(figure['curves'])<=3):
        raise ValueError('图解需要标题、坐标、说明、曲线和对应小节。')
    for curve in figure['curves']:
        if (not isinstance(curve,dict) or set(curve)!={'label','points'}
                or not isinstance(curve['label'],str) or not 1<=len(curve['label'])<=80
                or not isinstance(curve['points'],list) or not 2<=len(curve['points'])<=200):
            raise ValueError('图解曲线需要标签与有限坐标点。')
        for point in curve['points']:
            if (not isinstance(point,list) or len(point)!=2
                    or any(type(value) not in (int,float) or abs(value)>1e12 or not math.isfinite(value) for value in point)):
                raise ValueError('图解坐标必须为有限实数。')


def _legacy_cards():
    raw = _read(files(__package__).joinpath('knowledge_cards.json'))
    entries = []
    for card in raw['cards']:
        ident = card['id']
        if ident.startswith('gaussian-'):
            topic = 'laser'
        elif ident in ('fit-residuals','holdout','observation-design','csv-width-convention','fft-sampling','display-scales','film-reflectance-input'):
            topic = 'measurement'
        elif ident.startswith('optics-'):
            topic = 'foundations'
        else:
            topic = 'wave'
        entries.append({'id':ident,'title':card['title'],'terms':list(dict.fromkeys(card.get('terms',[])+card['aliases'])),
            'topic':topic,'level':'intro','license':'GPL-2.0-or-later','origin':'ORIGINAL','status':'PUBLISHED',
            'definition':card['definition'],'body':card['intuitive_text']+'\n\n'+card['detailed_text'],
            'example':card['verification'],'caution':'。'.join(card['common_mistakes']),
            'formula':card.get('formula'),'variables':card.get('variables'),'conditions':card['assumptions'],
            'prerequisites':card.get('prerequisite_ids',[]),'related':[],
            'sources':[raw['sources'][key]|{'rights':'REFERENCE_ONLY','section':raw['sources'][key]['title'],
                       'license':'参见原站条款'} for key in card['source_refs']],
            'review':'从 rc15 迁移的既有知识卡','pack':'rc15-cards','version':'0.2.15'})
    return entries


class Catalog:
    def __init__(self, packs, topics=()):
        packs=tuple(packs)
        self.entries = {entry['id']:entry for entry in _legacy_cards()}
        self.packs = [{'id':'rc15-cards','version':'0.2.15','published':len(self.entries),'drafts':0}]
        titles = {normalize(e['title']) for e in self.entries.values()}
        self.sources = {s['url']:s for e in self.entries.values() for s in e['sources']}
        pack_ids = {'rc15-cards'}
        for pack in packs:
            validate_pack(pack)
            if pack['id'] in pack_ids:raise ValueError(f'知识包编号冲突 {pack["id"]}')
            pack_ids.add(pack['id'])
            published = 0
            for item in pack['entries']:
                if item['status']!='PUBLISHED':continue
                if item['id'] in self.entries or normalize(item['title']) in titles:
                    raise ValueError(f'已发布条目冲突 {item["id"]}')
                sources, positions, source_indices = [], {}, {}
                for key in item['sources']:
                    source = pack['sources'][key]
                    locator = (source['url'],source['section'])
                    if locator not in positions:
                        positions[locator] = len(sources)
                        sources.append(source)
                    source_indices[key] = positions[locator]
                self.sources.update({s['url']:s for s in sources})
                claims = [{'kind': claim['kind'], 'statement': claim['statement'],
                           'source_indices': list(dict.fromkeys(source_indices[ref] for ref in claim['sources']))}
                          for claim in item.get('claims', ())]
                self.entries[item['id']] = item|{'sources':sources,'claims':claims,
                                                'pack':pack['id'],'version':pack['version']}
                titles.add(normalize(item['title']));published += 1
            self.packs.append({'id':pack['id'],'version':pack['version'],'published':published,'drafts':len(pack['entries'])-published})
        for ident, entry in self.entries.items():
            for other in entry['prerequisites']+entry['related']:
                if other not in self.entries or other==ident:raise ValueError(f'无效知识关系 {ident} → {other}')
        try:
            TopologicalSorter({i:e['prerequisites'] for i,e in self.entries.items()}).prepare()
        except CycleError as exc:
            raise ValueError('前置知识存在循环。') from exc
        topics=tuple(topics)+tuple(topic for pack in packs for topic in pack.get('topics',[]))
        self.study_topics = {topic['id']: topic for topic in topics}
        if len(self.study_topics) != len(topics):
            raise ValueError('专题编号重复。')
        for topic in topics:
            ids = [section['id'] for section in topic['sections']]
            if (not IDENTIFIER.fullmatch(topic['id']) or len(ids) != len(set(ids)) or len(ids) < 3
                or any(not IDENTIFIER.fullmatch(ident) for ident in ids)
                or any(ref not in self.entries for ref in topic['prerequisites'])
                or any(len(section['text']) < 45 or not section['references']
                       or any(ref not in self.entries for ref in section['references']) for section in topic['sections'])):
                raise ValueError(f'专题正文或依据有误 {topic["id"]}')
        self.conventions = tuple(item for item in CONVENTIONS if all(ref in self.entries for ref in item['entries']))
        self.version = digest({'entries':self.entries,'study_topics':self.study_topics,
                               'conventions':self.conventions,'experiment_links':CAPABILITIES,
                               'retrieval_revision':'20261114-script-boundaries-v3'})
        self._lock = Lock()
        self._index = sqlite3.connect(':memory:', check_same_thread=False)
        self._terms = {}
        self._content_tokens = {}
        self._fts = True
        try:
            self._index.execute('CREATE VIRTUAL TABLE articles USING fts5(id UNINDEXED, title, terms, body, tokenize="unicode61")')
        except sqlite3.OperationalError as exc:
            if 'no such module' not in str(exc):raise
            self._fts = False
        for ident, entry in self.entries.items():
            aliases = [entry['title'], *entry['terms']]
            self._terms[ident] = tuple(dict.fromkeys(normalize(alias) for alias in aliases))
            body = ' '.join((entry['definition'],entry['body'],entry['example'],entry['caution'],entry.get('formula') or '',entry.get('variables') or ''))
            self._content_tokens[ident] = set(tokens(' '.join(aliases)+' '+body))
            if self._fts:
                self._index.execute('INSERT INTO articles VALUES (?,?,?,?)',(ident,' '.join(tokens(entry['title'])),
                    ' '.join(tokens(' '.join(aliases))), ' '.join(tokens(body))))
        self._term_counts=Counter(term for terms in self._terms.values() for term in terms)
        self._index.commit()

    def close(self):
        self._index.close()

    def brief(self, ident):
        e = self.entries[ident]
        return {k:e[k] for k in ('id','title','topic','level','definition','license','pack','version')}|{
            'source':e['sources'][0]['title'],'has_formula':bool(e.get('formula')),
            'capability':'EXPERIMENT_READY' if ident in CAPABILITIES else 'FORMULA_EXAMPLE' if e.get('formula') else 'THEORY_ONLY',
        }

    def article(self, ident):
        e = self.entries[ident]
        experiment = CAPABILITIES.get(ident)
        return e|self.brief(ident)|{'catalog_version':self.version,
            'prerequisite_links':[self.brief(other) for other in e['prerequisites']],
            'related_links':[self.brief(other) for other in e['related']],
            'conventions':self.conventions_for((ident,)),
            'experiment':{'label':experiment[0],'url':'/experiments'+experiment[1]} if experiment else None}

    def conventions_for(self, references):
        refs = set(references)
        return [item for item in self.conventions if refs.intersection(item['entries'])]

    def overview(self):
        counts=Counter(e['topic'] for e in self.entries.values())
        return {'catalog_version':self.version,'published':len(self.entries),'source_count':len(self.sources),
            'formula_count':sum(bool(e.get('formula')) for e in self.entries.values()),
            'packs':[p|{'title':PACK_TITLES.get(p['id'],p['id'])} for p in self.packs],
            'topics':[{'id':key,'title':name,'count':counts[key]} for key,name in TOPICS.items()],
            'levels':LEVELS,'licenses':LICENSES,'index':'FTS5' if self._fts else 'LOCAL_SCAN',
            'study_topics':[{'id':topic['id'],'title':topic['title'],'intro':topic['intro']}
                            for topic in self.study_topics.values()],
            'paths':[{'id':key,'title':name,'entries':[self.brief(i) for i in chain],
                      'lesson':self.study_topics.get(PATH_LESSONS.get(key),{}).get('id')}
                     for key,(name,chain) in PATHS.items() if all(i in self.entries for i in chain)]}

    def named(self, question, ranked, *, contextual=False):
        """Prefer complete terms over shorter aliases occupying the same span."""
        text = normalize(question)
        spans = []
        for ident in ranked:
            for term in self._terms[ident]:
                if not term or term in _STOP or contextual and term in _WEAK or not term_matches(term,text):
                    continue
                for hit in re.finditer(re.escape(term),text):
                    if contextual and re.search(r'(?:别做|别讲|不做|不谈|不要讲|先不讨论).{0,8}$',text[:hit.start()]):
                        continue
                    spans.append((hit.start(),hit.end(),ident))
        intact = {ident for start,end,ident in spans if not any(
            other_start <= start and end <= other_end and other_end-other_start > end-start
            for other_start,other_end,_ in spans)}
        return [ident for ident in ranked if ident in intact]

    def exact(self, question, ranked):
        term=lookup_term(question)
        return [ident for ident in ranked if term and term in self._terms[ident]]

    def definition_matches(self, question, ranked):
        exact=self.exact(question,ranked)
        if exact:
            return exact
        term=lookup_term(question)
        if (not re.fullmatch(r'[\u3400-\u9fff]{2,10}',term)
                or term in _STOP or term==normalize(question)):
            return []
        # Some entries use descriptive titles (for example “焦深的像方容差”).
        prefix = term + '的'
        return [ident for ident in ranked if self._terms[ident][0].startswith(prefix)][:4]

    def definition_covered(self, question, ranked):
        """A component of a named effect does not cover the whole term."""
        term = normalize(requested_definition(question))
        if not term or len(term)>32 or self.definition_matches(question,ranked):
            return True
        return any(term_matches(term, name) for ident in ranked for name in self._terms[ident])

    def supports(self, question, ranked):
        question_tokens = set(tokens(question))
        if not question_tokens or not ranked:
            return False
        # In mixed-language questions, an unknown named method should not turn
        # into an answer about one of its common Chinese component words.
        if re.search(r'[\u3400-\u9fff]',question):
            latin = {t for t in question_tokens if t.isascii() and t.isalpha() and len(t)>=3}
            if latin - set().union(*(self._content_tokens[i] for i in ranked)):
                return False
        if self.named(question,ranked):
            return True
        frequencies = Counter(t for words in self._content_tokens.values() for t in question_tokens & words)
        informative = {t for t in question_tokens if frequencies[t] <= len(self.entries)*0.18}
        minimum=min(5,max(3,len(informative)*0.27))
        return any(len(informative & self._content_tokens[i]) >= minimum for i in ranked)

    def _ranked(self, question):
        q=normalize(question)
        lookup=lookup_term(question)
        matches={}
        for ident, terms in self._terms.items():
            title=terms[0]
            match=max((150 if t==lookup and t==title else 110 if t==lookup else
                       min(len(t)*3,52)+(12 if t==title else 0)+(20 if self._term_counts[t]==1 else 0)
                       for t in terms if t and (t==lookup or t not in _STOP and term_matches(t,q))),default=0)
            if (re.fullmatch(r'[\u3400-\u9fff]{2,10}',lookup)
                    and lookup not in _STOP and lookup!=q and title.startswith(lookup+'的')):
                match=max(match,90)
            if match:matches[ident]=match
        query=list(dict.fromkeys(tokens(q)))[:48]
        if query and self._fts:
            expression=' OR '.join('"'+t+'"' for t in query)
            with self._lock:
                rows=self._index.execute('SELECT id,bm25(articles,0,7,5,1) AS score FROM articles WHERE articles MATCH ? ORDER BY score',(expression,)).fetchall()
            for ident, score in rows:
                if score<-.7 or ident in matches:matches[ident]=matches.get(ident,0)+min(76,-score*1.4)
        elif query:
            query_set=set(query)
            for ident,e in self.entries.items():
                overlap=len(query_set.intersection(tokens(e['title']+' '+e['definition']+' '+e['body']+' '+e['example'])))
                if overlap>=min(2,len(query_set)):matches[ident]=matches.get(ident,0)+min(26,overlap*2)
        return sorted(matches,key=lambda ident:(-matches[ident],self.entries[ident]['title'],ident))

    def search(self, question='', *, topic='', level='', license='', capability='', pack='', page=1, limit=20):
        if not 1<=page<=10000 or not 1<=limit<=20 or len(question)>1600:
            raise ValueError('查询或分页超出范围。')
        ids=self._ranked(question) if question.strip() else list(self.entries)
        filtered=[]
        for ident in ids:
            item=self.brief(ident)
            if any(value and item[key]!=value for key,value in (('topic',topic),('level',level),('license',license),('capability',capability),('pack',pack))):continue
            filtered.append(item)
        start=(page-1)*limit
        study_topics = []
        query = set(tokens(question))
        if page==1 and query:
            active = {item['id'] for item in filtered[:5]}
            title_matches = []
            for lesson in self.study_topics.values():
                references = set(lesson['prerequisites'])
                references.update(ident for part in lesson['sections'] for ident in part['references'])
                references.update(prior for ident in tuple(references)
                                  for prior in self.entries[ident]['prerequisites'])
                named = len(query & set(tokens(lesson['title'])))
                linked = len(active & references)
                if linked and (linked>=2 or named>=min(2,len(query))):
                    title_matches.append((2*named+linked,lesson))
            title_matches.sort(key=lambda pair:(-pair[0],pair[1]['id']))
            study_topics = [{key:lesson[key] for key in ('id','title','intro')}
                            for _,lesson in title_matches[:3]]
        return {'catalog_version':self.version,'total':len(filtered),'page':page,'limit':limit,
                'items':filtered[start:start+limit],'study_topics':study_topics}


def builtin_packs():
    return tuple(_read(path) for path in sorted(DATA.iterdir(),key=lambda p:p.name) if path.name.startswith('pack-') and path.name.endswith('.json'))


def builtin_topics():
    path = DATA.joinpath('study-topics.json')
    return tuple(_read(path)) if path.is_file() else ()


def merge_packs(local):
    packs={pack['id']:pack for pack in builtin_packs()}
    seen=set()
    for pack in local:
        validate_pack(pack)
        ident=pack['id']
        if ident in seen or ident=='rc15-cards':
            raise ValueError(f'知识包编号冲突 {ident}')
        current=packs.get(ident)
        if current and current['version']==pack['version'] and current!=pack:
            raise ValueError('请为修改后的内置知识包使用新版本。')
        packs[ident]=pack
        seen.add(ident)
    return tuple(packs.values())


@lru_cache(maxsize=4)
def _snapshot(blobs):
    return Catalog(merge_packs(json.loads(blob) for blob in blobs), builtin_topics())


def local_packs(root):
    folder=Path(root)/'knowledge-packs'
    return sorted(p for p in folder.glob('*.json') if p.is_file() and not p.is_symlink())


def catalog(root):
    return _snapshot(tuple(path.read_bytes() for path in local_packs(root)))


def fingerprint(data):
    return 'sha256:'+sha256(data).hexdigest()
