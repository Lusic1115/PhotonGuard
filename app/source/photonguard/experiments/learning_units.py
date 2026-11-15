"""Common length syntax for the learning workbench."""

NUMBER = r'\d+(?:\.\d+)?|\.\d+'
UNIT = r'nm|纳米|[μµu]m|微米|mm|毫米|cm|厘米|m|米'
DISTANCE = rf'({NUMBER})\s*({UNIT})'
_IN_MM = {'nm': 1e-6, '纳米': 1e-6, 'um': .001, '微米': .001,
          'mm': 1, '毫米': 1, 'cm': 10, '厘米': 10, 'm': 1000, '米': 1000}


def length(number, unit, multiplier=1):
    unit = unit.lower().replace('μ', 'u').replace('µ', 'u')
    return float(f'{float(number) * _IN_MM[unit] * multiplier:.13g}')
