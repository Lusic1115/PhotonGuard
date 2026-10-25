"""Source-bound local templates for exact, published homepage examples.

This is template selection, not free-text classification. An edited sentence
must go through the normal source checks; it cannot inherit this snapshot.
"""
from ..core import digest

SINGLE_SLIT_ID = 'homepage-single-slit-20261021-v1'
SINGLE_SLIT_TEXT = '我想做单缝衍射演示，看看缝宽怎样改变中央亮区。采用均匀相干照明、标量近轴模型，在理想薄透镜的后焦面观察。波长633 nm，透镜焦距200 mm，缝高100 μm，初始缝宽20 μm。只比较缝宽16、20、24 μm，其余物理条件保持不变。希望中心到第一暗纹的距离为6.33 mm，允许偏差0.05 mm。数值输入平面窗口0.4 mm，网格256。只做快速计算，不做Meep。请先整理条件和计划，等我确认后再计算，并显示曲线与二维光强分布。'


def match(text):
    if text != SINGLE_SLIT_TEXT:
        return None
    return {'id': SINGLE_SLIT_ID, 'case_id': 'D01',
            'text_sha256': digest(text), 'source': 'BUILTIN_EXAMPLE_TEMPLATE'}


def eligible(record, data, text, case_id):
    if (data.get('type') not in ('plan', 'library_plan')
            or data.get('mode') != 'rules' or data.get('request') is not None
            or record.get('kind') != 'specified'
            or record.get('parent_id') or record.get('parent_context')
            or record.get('approved_plan_sha256') or record.get('result')
            or text != record.get('text') or case_id not in (None, 'D01')):
        return None
    example = match(text)
    if not example:
        return None
    state = record.get('fact_state')
    if state and (state.get('case_id') != example['case_id'] or
                  not state.get('events') or
                  any(e.get('interpretation', {}).get('example') != example
                      for e in state['events'])):
        return None
    return example


def interpretation(text, message_id):
    example = match(text)
    if not example:
        raise ValueError('内置示例原文已修改，请重新核对条件')
    bindings = [
        ('parameters.aperture', 'single_slit', '单缝衍射'),
        ('parameters.wavelength_nm', 633, '波长633 nm'),
        ('parameters.focal_length_mm', 200, '透镜焦距200 mm'),
        ('parameters.slit_height_um', 100, '缝高100 μm'),
        ('parameters.slit_width_um', 20, '初始缝宽20 μm'),
        ('parameters.window_mm', 0.4, '数值输入平面窗口0.4 mm'),
        ('parameters.grid_size', 256, '网格256'),
        ('recipe.values', [16, 20, 24], '只比较缝宽16、20、24 μm'),
        ('goal.metric', 'first_zero_mm', '中心到第一暗纹的距离'),
        ('goal.operator', 'near', '距离为6.33 mm，允许偏差0.05 mm'),
        ('goal.target', 6.33, '中心到第一暗纹的距离为6.33 mm'),
        ('goal.tolerance', 0.05, '允许偏差0.05 mm'),
        ('execution.fast_only', True, '只做快速计算，不做Meep'),
    ]
    operations = []
    for field, value, quote in bindings:
        start = text.index(quote)
        operations.append({'field': field, 'value': value, 'correction': True,
                           'evidence': [{'message_id': message_id, 'start': start,
                                         'end': start + len(quote)}]})
    return {'example': example, 'confirmed_operations': operations,
            'ambiguities': [], 'proposed_operations': [], 'retained_fields': [],
            'consumed_spans': [(0, len(text))]}
