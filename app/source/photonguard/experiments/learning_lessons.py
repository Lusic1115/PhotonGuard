"""Guided questions around existing approved experiments and saved results."""

import json

from ..workflow import WorkflowError, atomic_json
from ..workflow_model import wire


SLIT_TASK = ('使用 633 nm 光和 200 mm 焦距透镜，希望单缝中央亮区全宽 10±0.2 mm。'
             '只允许改变缝宽，范围 15–35 μm。采用教学模型，其他设置请帮我安排。')

LESSONS = {
    'slit-width': {
        'title': '单缝越宽，亮区会怎样？',
        'model': '理想薄透镜焦平面单缝',
        'concepts': '中央亮区全宽、第一暗纹',
        'held': '保持波长、焦距、缝高和采样条件。',
        'experiment': '批准后比较不同缝宽。切换两个候选，在横向中心曲线上分别查看左右第一暗纹。',
        'prediction': '缝变宽时，左右第一暗纹之间的距离会怎样？',
        'options': ('保持相同', '变小', '变大', '暂时不确定'),
        'observe': '比较候选表的中央全宽与曲线上的第一暗纹。半高全宽在峰值的一半处读数，使用另一对位置。',
        'ask': '用这版单缝的图讲讲，第一暗纹、中央亮区全宽和半高全宽分别在哪？',
        'transfer': '如果要让中央亮区更接近目标全宽，应该比较哪个量？',
    },
    'slit-compensation': {
        'title': '波长变了，能保持亮区宽度吗？',
        'model': '理想薄透镜焦平面单缝',
        'concepts': '波长、焦距、缝宽与中央全宽的关系',
        'held': '先取得参照图。之后保持焦距，按允许范围调整缝宽；也可保持缝宽并调整焦距。',
        'experiment': '计算参照图后，用保宽预览试试 532 nm。核对建议与原值，再决定是否批准验证。',
        'prediction': '波长变短、焦距固定，保持原宽度需要怎样调整缝宽？',
        'options': ('增大缝宽', '沿用缝宽', '减小缝宽', '暂时不确定'),
        'observe': '先看解析横向全宽，再叠加两版场曲线。波长和缝宽同比例变化会保住横向尺度；缝高固定时还要检查纵向。',
        'ask': '结合我选中的候选，为什么同时改波长和缝宽有可能保持横向中央亮区全宽？',
        'transfer': '如果没有合适的缝片而焦距可调，需要把焦距往哪个方向改？',
    },
    'slit-intensity': {
        'title': '图更亮，就更接近目标吗？',
        'model': '理想薄透镜焦平面单缝与 FFT 场',
        'concepts': '共同入射光强、峰值归一化、横向与纵向',
        'held': '对同一候选先只改显示标尺。物理对照时只改缝高。',
        'experiment': '切换共同标尺、峰值归一化和分贝图。想验证装置变化时，从图下方预览缝高改动并另存。',
        'prediction': '只切换到峰值归一化，计算出的中央全宽和达标状态会怎样？',
        'options': ('两者都保持', '中央全宽改变', '达标状态改变', '暂时不确定'),
        'observe': '同一候选切换标尺会改变显示值。改缝高后，请分别检查横向与纵向，并在目标表查看实际评判。',
        'ask': '同一候选按峰值归一化后变亮，为什么达标状态仍要看中央全宽？缝高又影响哪个方向？',
        'transfer': '想比较两版绝对峰值时，应该选哪种标尺？',
    },
    'double-slit': {
        'title': '双缝的包络、条纹和中心怎样变化？',
        'model': '理想焦平面双缝',
        'template': 'diffraction-phase',
        'concepts': '双缝中心距、单缝宽度、相对相位',
        'held': '每轮只改一个量，并保留波长、焦距及其余几何条件。',
        'experiment': '先核对不同相位的三个候选，再选缝宽或中心距做一轮有批准的对照。',
        'prediction': '只增大双缝中心距时，相邻条纹间距会怎样？',
        'options': ('变小', '变大', '保持相同', '暂时不确定'),
        'observe': '在同一光强标尺下分别找相邻条纹、包络和中心亮暗，逐项对应中心距、缝宽和相位。',
        'ask': '用选中的双缝图，帮我区分条纹间距、外层包络和中心亮暗。',
        'transfer': '条纹间距看起来相同，改了什么还能让中心由亮变暗？',
    },
    'gaussian-waist': {
        'title': '屏幕移动，最细的光斑会跟着走吗？',
        'model': '理想 TEM00 高斯光束',
        'template': 'gaussian-catalog',
        'concepts': '束腰位置、屏幕位置、同截面 1/e² 强度半径',
        'held': '在对照中选择同一透镜和同一入射光束，只移动屏幕。',
        'experiment': '先计算三个透镜候选，选中一个，再从结果图下方改屏幕位置并批准对照。',
        'prediction': '固定透镜、移动屏幕后，新束腰位置会怎样？',
        'options': ('随屏幕一起移动', '保持原位置', '一定朝反方向移动', '暂时不确定'),
        'observe': '把曲线最低处与当前屏幕标记分别读出。比较两版屏幕半径和新束腰位置。',
        'ask': '用当前高斯结果解释屏幕上最小光斑和传播中的新束腰。只移动屏幕会发生什么？',
        'transfer': '若要移动真正的束腰，可以尝试改哪一个物理条件？',
    },
    'film-ambiguity': {
        'title': '为什么一次反射率可能对应多个膜厚？',
        'model': '已知折射率的单层膜 · 教学合成数据',
        'inverse': 'single_film_known_index_v1',
        'concepts': '薄膜相位、候选厚度、独立观测',
        'held': '已知折射率、正入射模型与厚度搜索范围。',
        'experiment': '核对并拟合起始反射率。比较允许波长的预测差异，批准一项教学补测，再用两份观测联合分析。',
        'prediction': '只知道一个波长的反射率，在有限搜索范围内可能有几组相符的厚度？',
        'options': ('总是唯一', '可能多组', '一定没有', '暂时不确定'),
        'observe': '比较前后候选、所用波长与厚度区间。联合结果描述这组观测和当前搜索范围。',
        'ask': '结合当前膜厚结果，为什么要换一个波长再观测？如何理解候选只剩一个？',
        'transfer': '如果候选在某个可用波长的预测几乎一样，这次观测能增加什么信息？',
    },
}


def catalog():
    return [{'id': ident, 'title': lesson['title'], 'model': lesson['model']} for ident, lesson in LESSONS.items()]


def start(store, data):
    ident = data.get('lesson_id')
    if set(data) != {'lesson_id'} or not isinstance(ident, str) or ident not in LESSONS:
        raise WorkflowError('请选择一个教学任务。')
    lesson = LESSONS[ident]
    with store.lock:
        if 'inverse' in lesson:
            from .observation_group import create
            record = create(store, {'model': lesson['inverse'], 'teaching': True})
        elif 'template' in lesson:
            from .planner import build_plan
            record = store.create({'kind': 'teaching', 'template_id': lesson['template']})
            plan = build_plan(record['spec'])
            record.update(status='PLANNED', plan=plan, spec=plan['spec'])
        else:
            from . import slit_goal
            inputs = {'text': SLIT_TASK, 'teaching': True}
            preview = slit_goal.prepare_goal(inputs)
            response = slit_goal.save_goal(store, {**inputs, 'preview_sha256': preview['preview_sha256']})
            record = store.get(response['case']['id'])
        record.update(learning_lesson_id=ident, title=lesson['title'])
        store.save(record)
        store.event(record, 'LEARNING_LESSON_SELECTED', {'lesson_id': ident})
        return {'status': 'CREATED', 'case': store.public_record(record)}


def _note_context(store, record):
    root = store.get(record['family_id']) if record.get('parent_id') else record
    ident = root.get('learning_lesson_id')
    if (record['domain'] != root['domain'] or record.get('estimate_origin')
            or (record['kind'] == 'inverse') != (root['kind'] == 'inverse')):
        return None, None
    lesson = ident if ident in LESSONS else None
    path = store.owner.safe('experiments', root['id'], 'learning-notes.json') if lesson or root.get('slit_goal') else None
    return lesson, path


def _notes(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def state(store, record):
    ident, path = _note_context(store, record)
    if not ident:
        return None
    lesson = LESSONS[ident]
    notes = _notes(path)
    current = notes.get(record['id'], {})
    prediction = next(({'record_id': source, 'text': notes[source]['prediction']}
                       for source in (record['id'], record.get('parent_id'), record['family_id'])
                       if 'prediction' in notes.get(source, {})), None)
    return {'id': ident, **{k: v for k, v in lesson.items() if k not in ('template', 'inverse')},
            'saved_prediction': prediction, 'reflection': current.get('reflection')}


def goal_reflection(store, record):
    if record['status'] != 'COMPLETED' or not record.get('result'):
        return None
    ident, path = _note_context(store, record)
    if not ident and path:
        return {'text': _notes(path).get(record['id'], {}).get('reflection', '')}
    return None


def study_hypothesis(store, record):
    _, path = _note_context(store, record)
    if path:
        return {'text': _notes(path).get(record['id'], {}).get('hypothesis', '')}
    return None


def study_history(store, record):
    _, path = _note_context(store, record)
    if not path:
        return []
    notes = _notes(path)
    history, seen = [], set()
    while record['id'] not in seen:
        seen.add(record['id'])
        entry = notes.get(record['id'], {})
        if entry or record.get('slit_followup', {}).get('verification'):
            history.append((record, entry))
        if not record.get('parent_id'):
            break
        parent = store.get(record['parent_id'])
        if parent['family_id'] != record['family_id'] or parent['domain'] != record['domain']:
            break
        record = parent
    return list(reversed(history))


def respond(store, data):
    if set(data) != {'record_id', 'kind', 'text'} or not isinstance(data.get('text'), str):
        raise WorkflowError('请核对学习记录。')
    kind, value = data['kind'], data['text'].strip()
    with store.lock:
        record = store.get(data['record_id'])
        ident, path = _note_context(store, record)
        if not path:
            raise WorkflowError('这条实验记录没有可填写的学习记录。', 409)
        if kind == 'prediction':
            if not ident or record['status'] not in ('DRAFT', 'PLANNED', 'NEEDS_INFORMATION') or value not in LESSONS[ident]['options']:
                raise WorkflowError('请在开始本版计算前选择一个预测。', 409)
        elif kind == 'reflection':
            if record['status'] != 'COMPLETED' or not 1 <= len(value) <= 600 or wire.credential_risk(value):
                raise WorkflowError('请基于已完成的结果，填写 1–600 字观察，并移除密钥。')
        elif kind == 'hypothesis':
            if record['status'] != 'COMPLETED' or not 1 <= len(value) <= 600 or wire.credential_risk(value):
                raise WorkflowError('请填写对下一次对照的预测，限 1–600 字。')
        else:
            raise WorkflowError('未知的学习记录类型。')
        notes = _notes(path)
        notes.setdefault(record['id'], {})[kind] = value
        atomic_json(path, notes)
        if kind == 'hypothesis':
            return {'study_hypothesis': study_hypothesis(store, record)}
        return {'lesson': state(store, record)} if ident else {'goal_reflection': {'text': value}}
