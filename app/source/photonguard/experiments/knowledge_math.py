"""Small, registered learning calculations. No experiment or external execution."""

import math
import re
import unicodedata

from .knowledge_retrieval import canonical_notation


C = 299_792_458
NUMBER = r'(?:\d{1,10}(?:\.\d{1,10})?|\.\d{1,10})'
UNSIGNED = rf'(?<![\d.+−-]){NUMBER}'
LENGTH = {'pm':1e-12, 'nm':1e-9, 'μm':1e-6, 'um':1e-6, 'mm':1e-3, 'cm':1e-2, 'm':1.0}
WATTS = {'fw':1e-15, 'pw':1e-12, 'nw':1e-9, 'μw':1e-6, 'uw':1e-6, 'mw':1e-3, 'w':1.0}
HERTZ = {'hz':1.0, 'khz':1e3, 'mhz':1e6, 'ghz':1e9}
NEP = re.compile(rf'({UNSIGNED})\s*(fw|pw|nw|μw|uw|mw|w)\s*/\s*(?:√\s*(?:hz|\(\s*hz\s*\))|sqrt\s*\(\s*hz\s*\))', re.I)
BANDWIDTH = re.compile(rf'({UNSIGNED})\s*(ghz|mhz|khz|hz)(?![a-z])', re.I)
REVISE = re.compile(r'(?:第\s*([一二12])\s*(?:台|个|路)|(?<![a-z0-9])([ab])\s*(?:台|设备|通道)?)'
                    r'\s*的?\s*(?:(?:等效(?:噪声)?)?带宽\s*)?(?:改|设为|调整到|换成)')


def _record(ident, title, formula, inputs, result, condition, *references):
    return {'id':ident, 'title':title, 'formula':formula, 'inputs':inputs, 'result':result,
            'condition':condition, 'references':list(references)}


def _positive(matches, units):
    quantities = [float(number)*units[unit] for number, unit in matches]
    return quantities if all(0 < value < 1e15 for value in quantities) else []


def _nep(question, prior):
    if re.search(r'不是白噪声|非白噪声|谱不平坦|coloured noise|colored noise',question):
        return None
    density = _positive(NEP.findall(question), WATTS)
    bandwidths = _positive(BANDWIDTH.findall(question), HERTZ)
    # One named second device is not a two-device comparison. Do not relabel
    # its values as device 1 or silently duplicate them for the other device.
    if len(density) == 1 and len(bandwidths) == 1 and re.search(r'第\s*[二2]\s*(?:台|个|路)|(?<![a-z])b\s*(?:探测器|设备|通道|带宽)', question):
        return None
    revision = REVISE.search(question)
    if revision and len(bandwidths) == 1 and not density and prior and prior['id'] == 'nep-bandwidth':
        inputs = prior['inputs']
        target = revision[1] or revision[2]
        index = 0 if target in ('一','1','a') else 1
        if len(inputs) != 2:
            return None
        density = [item['nep_w_per_sqrt_hz'] for item in inputs]
        updated = [item['bandwidth_hz'] for item in inputs]
        updated[index] = bandwidths[0]
        bandwidths = updated
    if len(density) not in (1,2) or len(density) != len(bandwidths):
        return None
    pairs = [{'nep_w_per_sqrt_hz':nep, 'bandwidth_hz':bandwidth}
             for nep, bandwidth in zip(density, bandwidths)]
    text = '；'.join(f'第 {i} 台：{nep/1e-12:.7g} pW/√Hz × √({bandwidth:.7g} Hz) = {nep*math.sqrt(bandwidth)/1e-12:.7g} pW'
                    for i, (nep, bandwidth) in enumerate(zip(density, bandwidths),1))
    return _record('nep-bandwidth', '等效输入均方根噪声', 'P_rms = NEP_s √B', pairs, text,
                   '按题给带宽为等效噪声带宽、频带内白噪声计算。各设备分别使用自己的带宽。',
                   'detector-nep','detector-noise-bandwidth')


def _detectivity(question):
    if not re.search(r'd\*|比探测率|jones',question):
        return None
    area = _positive(re.findall(rf'({UNSIGNED})\s*(mm|cm|m)(?:\^?2)(?![a-z0-9])',question),
                     {'mm':0.01,'cm':1,'m':10000})
    density = _positive(NEP.findall(question), WATTS)
    if len(area) != 1 or len(density) != 1:
        return None
    root_area = math.sqrt(area[0])
    value = root_area/density[0]
    return _record('detectivity', '比探测率', 'D* = √A/NEP_s', {'area_cm2':area[0], 'nep_w_per_sqrt_hz':density[0]},
                   f'面积换算为 {area[0]:.7g} cm²，面积平方根为 {root_area:.7g} cm。'
                   f'D* = {value:.7g} cm·√Hz/W（Jones）。',
                   'NEP 已用 W/√Hz 归一，面积采用光敏面积。','detector-specific-detectivity')


def _spectral_density(question):
    """Convert power density at a stated vacuum wavelength through its Jacobian."""
    power = r'fw|pw|nw|μw|uw|mw|w'
    axis = r'thz|ghz|mhz|khz|hz|pm|nm|μm|um|mm|cm|m'
    densities = list(re.finditer(rf'({UNSIGNED})\s*({power})\s*/\s*({axis})(?![a-z])', question))
    if len(densities) != 1:
        return None
    source = densities[0]
    # The wavelength is read outside the density token. A channel bandwidth
    # must not become the wavelength and density units must not become a gap.
    rest = question[:source.start()] + ' ' + question[source.end():]
    waves = re.findall(rf'({UNSIGNED})\s*(pm|nm|μm|um|mm|cm|m)(?![a-z])', rest)
    targets = re.findall(rf'({power})\s*/\s*({axis})(?![a-z])', rest)
    if len(waves) != 1 or len(targets) != 1:
        return None
    wavelength = float(waves[0][0]) * LENGTH[waves[0][1]]
    value, source_power, source_axis = source.groups()
    target_power, target_axis = targets[0]
    frequency_units = HERTZ | {'thz':1e12}
    forward = source_axis in LENGTH and target_axis in frequency_units
    reverse = source_axis in frequency_units and target_axis in LENGTH
    if wavelength <= 0 or not (forward or reverse):
        return None
    units = LENGTH | frequency_units
    density_si = float(value) * WATTS[source_power] / units[source_axis]
    jacobian = wavelength**2 / C if forward else C / wavelength**2
    output = density_si * jacobian * units[target_axis] / WATTS[target_power]
    display_axis = {'hz':'Hz','khz':'kHz','mhz':'MHz','ghz':'GHz','thz':'THz'}.get(target_axis,target_axis)
    target = target_power.replace('w','W') + '/' + display_axis
    return _record('spectral-density-jacobian','光谱功率密度换元',
                   'Sλ |dλ| = Sν |dν|，Sν = Sλ λ₀²/c（SI 单位）',
                   {'wavelength_m':wavelength,'density_value':float(value),
                    'source_unit':source_power+'/'+source_axis,'target_unit':target_power+'/'+target_axis,
                    'density_si':density_si,'jacobian_si':jacobian,'converted_value':output},
                   f'在 {wavelength*1e9:.9g} nm 处，换算结果为 {output:.10g} {target}。'
                   '先把输入密度换成 W/m 或 W/Hz，再乘绝对雅可比，最后换成目标单位。',
                   '按真空波长处的点谱密度换元；有限带宽总功率需要积分，横轴方向用绝对导数处理。',
                   'spectral-density-change-variable','frequency-axis-spacing-nonlinearity')


def _wavelength(question):
    if re.search(r'谱密度|spectral.?density|每纳米|每赫兹|[wW]\s*/\s*(?:nm|μm|um|[kmgt]?hz)',question):
        return None
    question = re.sub(r'(?<=\d)\s*纳米', ' nm', question)
    question = re.sub(r'(?<=\d)\s*微米', ' μm', question)
    if not re.search(r'fsr|共振|光速|频[率差]|ghz|thz|frequen',question):
        return None
    if re.search(r'和频|差频|倍频|闲频|混频|sum.frequency|difference.frequency',question):
        return None
    if re.search(rf'(?:周长|腔长|半径|直径|光程|路径长度)\s*(?:为|是|[=:：])?\s*{UNSIGNED}\s*(?:pm|nm|μm|um|mm|cm|m)(?![a-z])',question):
        return None  # A geometric length is not an optical wavelength.
    units = r'pm|nm|μm|um'
    values = re.findall(rf'({UNSIGNED})\s*({units})(?![a-z])',question)
    # A central wavelength and a small interval are not two absolute wavelengths.
    center = re.search(rf'({UNSIGNED})\s*({units})\s*(?:中心|附近|处)',question)
    center = center or re.search(rf'(?:中心波长|波长中心)\s*(?:为|是|[=:])?\s*({UNSIGNED})\s*({units})',question)
    if center and 1 <= len(values) <= 2:
        wave = float(center[1])*LENGTH[center[2]]
        gaps = list(values)
        gaps.remove((center[1],center[2]))
        gap = float(gaps[0][0])*LENGTH[gaps[0][1]] if gaps else None
        if wave <= 0 or gap is not None and not 0 < gap/wave <= .1:
            return None
        slope = C/wave**2
        text = (f'在 {wave*1e9:.8g} nm 处，|dν/dλ| = c/λ² = {slope:.9g} Hz/m'
                f' = {slope*1e-18:.9g} GHz/nm；1 Hz/m = 10⁻¹⁸ GHz/nm。')
        if gap is not None:
            text += f' {gap*1e9:.8g} nm 的局部波长间隔对应约 {slope*gap/1e9:.9g} GHz。'
        return _record('wavelength-local-gap','中心波长处的频率换算',
                       '|dν/dλ| = c/λ²，|Δν| ≈ c Δλ/λ²',
                       {'center_wavelength_nm':wave*1e9,'interval_nm':None if gap is None else gap*1e9,
                        'slope_hz_per_m':slope,'slope_ghz_per_nm':slope*1e-18},text,
                       '使用真空波长；间隔换算为小相对带宽的局部近似。','frequency-axis-spacing-nonlinearity')
    if len(values) == 1:
        pair = re.search(rf'({UNSIGNED})\s*(?:、|和|与|到|,|及|and|&)\s*({UNSIGNED})\s*({units})(?![a-z])',question)
        if pair:
            values = [(pair[1],pair[3]),(pair[2],pair[3])]
    waves = _positive(values, LENGTH)
    if len(waves) == 1:
        wave = waves[0]
        return _record('wavelength-frequency', '波长换算频率', 'ν = c/λ₀', {'wavelength_nm':round(wave*1e9,10)},
                       f'{wave*1e9:.8g} nm 对应 {C/wave/1e12:.8g} THz。',
                       'λ₀ 为真空波长。','phase-velocity')
    if len(waves) != 2 or waves[0] == waves[1]:
        return None
    a, b = waves
    freq_gap = C * abs(a-b)/(a*b)
    text = f'两点波长差为 {abs(a-b)*1e9:.8g} nm，频率差为 {freq_gap/1e9:.9g} GHz。'
    fsr = bool(re.search(r'fsr|微环|共振|同族|谱线|谐振',question))
    condition = ('按真空波长换算。只有当两条谱线属于相邻同一模族共振时，才把该间隔称为 FSR。'
                 if fsr else '按两点的真空波长换算。')
    return _record('wavelength-gap', '两点间的频率差', 'Δν = c|1/λ₁ − 1/λ₂|',
                   {'wavelengths_nm':[round(wave*1e9,10) for wave in waves]}, text, condition,
                   'microring-fsr' if fsr else 'phase-velocity')


def slit_scope_changed(question):
    return bool(re.search(r'(?:不满足|不在|不是|脱离|取消|没有|近场).{0,5}远场|非远场',question))


def _slit(question, prior):
    if slit_scope_changed(question):
        return None
    prior_inputs = prior['inputs'] if prior and prior['id']=='single-slit' else {}
    uses_slit = '单缝' in question or prior_inputs
    asks_scale = re.search(r'暗纹|中央.{0,5}(?:全宽|亮斑宽)|全宽',question)
    if not uses_slit or not asks_scale:
        return None
    units = r'pm|nm|μm|um|mm|cm|m'
    def quantities(label):
        return _positive(re.findall(rf'(?:{label})\s*(?:为|是|[:=]|取|改为|改成|换成)?\s*({UNSIGNED})\s*({units})(?![a-z])',
                                    question),LENGTH)
    focal = quantities('焦距|焦长')
    free = quantities('屏距|传播距离|屏幕距离')
    if focal and free:
        return None
    geometry = 'focal_plane' if focal else 'free_space' if free else prior_inputs.get('geometry','free_space')
    if prior_inputs and (focal or free) and geometry != prior_inputs.get('geometry','free_space'):
        prior_inputs = {}
    keys = ('wavelength_m','width_m','distance_m')
    labels = ('波长|单缝用','缝宽','焦距|焦长' if geometry=='focal_plane' else '屏距|传播距离|屏幕距离')
    inputs = {}
    for key,label in zip(keys,labels):
        values = quantities(label)
        if len(values)==1:
            inputs[key] = values[0]
        elif not values and key in prior_inputs and not re.search(rf'(?:{label})\s*(?:改|换|不确定)',question):
            inputs[key] = prior_inputs[key]
        else:
            return None
    wave, slit, distance = (inputs[key] for key in keys)
    if wave/slit > 0.1:
        return None
    x = wave*distance/slit
    length = 'f' if geometry=='focal_plane' else 'L'
    return _record('single-slit', '单缝暗纹与中央全宽', f'x₁ ≈ λ₀ {length}/a，W ≈ 2x₁',
                   inputs | {'geometry':geometry},
                   f'单侧第一暗纹距中心约 {x*1000:.7g} mm；中央全宽约 {x*2000:.7g} mm。',
                   ('假定理想薄透镜焦平面、近轴和法向入射。' if geometry=='focal_plane'
                    else '假定远场、小角度及法向入射。'),'slit-width-definitions','slit-scale')


def _waveplate(question):
    if not re.search(r'半波片|half.wave plate',question):
        return None
    matches = [re.search(rf'(?:{term})(?:是|为|\s|:|=)*(-?{NUMBER})\s*(?:°|度)',question)
               for term in ('入射(?:方向|角度|偏振方向)?','快轴(?:方向|角度)?')]
    if not all(matches):
        return None
    incoming, axis = (float(match[1]) for match in matches)
    return _record('half-waveplate', '半波片转动线偏振', 'α_out = 2θ − α_in (mod 180°)',
                   {'input_deg':incoming, 'axis_deg':axis},
                   f'2 × {axis:g}° − ({incoming:g}°) ≡ {(2*axis-incoming)%180:g}°（按 180° 取模）。',
                   '理想半波延迟与线偏振入射，两个输入角在同一坐标中。','half-waveplate')


def _camera(question):
    if re.search(r'不独立|非独立|不等方差|方差不同|有相关|噪声相关|相关帧|帧间相关|协方差|同步漂移|共同漂移',question):
        return None
    if not re.search(r'两(?:帧|幅|张)|差图|差分|相减',question):
        return None
    variances=re.findall(rf'({UNSIGNED})\s*dn\s*\^?2(?!\d)',question)
    deviations=re.findall(rf'(?:标准差|rms)\s*(?:为|是|=)?\s*({UNSIGNED})\s*dn(?!\s*\^?2)',question)
    if len(variances)==1 and not deviations and re.search(r'方差|variance',question):
        var=float(variances[0])/2
    elif len(deviations)==1 and not variances and re.search(r'独立',question) and re.search(r'同分布|等方差|同方差',question):
        var=float(deviations[0])**2/2
    else:
        return None
    return _record('camera-pair', '两帧差分换算单帧噪声', 'σ²_single = Var(A − B)/2',
                   {'difference_variance_dn2':2*var},
                   f'单帧方差为 {var:g} DN²，标准差为 {math.sqrt(var):.7g} DN。',
                   '两帧同条件且随机噪声独立同方差，差图方差使用去均值结果。',
                   'camera-temporal-noise-pair','camera-temporal-spatial-noise')


def _purity(question):
    if '纯度' not in question:
        return None
    values = re.search(rf'(?:对角(?:元|元素|线)|diag)\s*(?:为|是|:|=|[\[(])*\s*({NUMBER})\s*[,、，]\s*({NUMBER})',question)
    if not values:
        return None
    a, b = float(values[1]), float(values[2])
    if not math.isclose(a+b,1,abs_tol=1e-10) or not re.search(r'对角(?:矩阵|态)|diag|非对角(?:元|项)为\s*0',question):
        return None
    return _record('qubit-diagonal-purity', '对角二能级态的纯度', 'Tr(ρ²) = p₀² + p₁²',
                   {'diagonal':[a,b]}, f'{a:g}² + {b:g}² = {a*a+b*b:.8g}。',
                   '密度矩阵在给定基底中为对角矩阵，两个非负布居和为一。','quantum-state-purity')


def _mixing_wavelengths(question):
    sum_frequency = bool(re.search(r'和频|sum.frequency',question))
    difference = bool(re.search(r'差频|difference.frequency',question))
    if not (sum_frequency or difference):
        return None
    waves = _positive(re.findall(rf'({UNSIGNED})\s*(nm|μm|um)(?![a-z])',question),LENGTH)
    if len(waves)!=2:
        return None
    a,b = (1/wave for wave in waves)
    outputs,refs = [],[]
    def describe(label, inverse_wavelength):
        return (f'{label}真空波长 {1e9/inverse_wavelength:.7g} nm，'
                f'频率 {C*inverse_wavelength/1e12:.7g} THz。' if inverse_wavelength else f'{label}为零频。')
    if sum_frequency:
        outputs.append(describe('和频',a+b))
        refs.append('sum-frequency-conversion')
    if difference:
        outputs.append(describe('差频',abs(a-b)))
        refs.append('difference-frequency-conversion')
    return _record('mixing-vacuum-wavelength','和频与差频波长',
                   '1/λ₊=1/λ₁+1/λ₂，1/λ₋=abs(1/λ₁−1/λ₂)',
                   {'wavelengths_nm':[wave*1e9 for wave in waves]},' '.join(outputs),
                   '按真空波长换算频率。模式匹配及输出功率需要另核对。',*refs)


def _conversion_mismatch_width(question):
    if not re.search(r'相位失配|失配主瓣',question) or not re.search(r'半高|零点',question):
        return None
    if not re.search(r'小信号|未耗尽|均匀',question):
        return None
    raw = re.search(rf'(?:晶体|波导)(?:长|长度)?\s*(?:为|是|:|=)?\s*({UNSIGNED})\s*(mm|cm|m)(?![a-z])',question)
    if not raw:
        return None
    length = float(raw[1])*LENGTH[raw[2]]
    if length <= 0:
        return None
    return _record('nonlinear-mismatch-width','转换失配曲线的宽度',
                   'FWHM≈5.566/L，第一零点=±2π/L',
                   {'length_m':length},
                   f'失配半高全宽约 {5.566/length:.6g} m⁻¹，第一零点为 ±{2*math.pi/length:.6g} m⁻¹。',
                   '均匀小信号平面波，忽略吸收。sinc(u)=sin(u)/u。',
                   'nonlinear-mismatch-fwhm-conversion','nonlinear-mismatch-first-zero')


def _gaussian_gdd(question):
    if not (re.search(r'gaussian|高斯',question) and re.search(r'变换极限|无啁啾',question) and
            re.search(r'二阶谱相位|gdd|群延迟色散',question)):
        return None
    duration = re.findall(rf'({UNSIGNED})\s*fs(?![a-z0-9^])',question)
    gdd = re.findall(rf'(-?{NUMBER})\s*fs\^?2(?![a-z0-9])',question)
    if len(duration)!=1 or len(gdd)!=1 or float(duration[0])<=0:
        return None
    initial,phase = float(duration[0]),float(gdd[0])
    final = initial*math.hypot(1,4*math.log(2)*phase/initial**2)
    return _record('gaussian-gdd','Gaussian 脉冲的二阶色散展宽',
                   'T_out=T_in√[1+(4ln2·GDD/T_in²)²]',
                   {'initial_fwhm_fs':initial,'gdd_fs2':phase},
                   f'代入 {initial:g} fs 和 {phase:g} fs²，输出强度半高全宽约 {final:.6g} fs。',
                   '输入为变换极限 Gaussian。假定题给脉宽为强度 FWHM，只有二阶谱相位。',
                   'ultrafast-gaussian-gdd-broadening')


def _mean_plane_wave_comparison(question):
    if not (re.search(r'峰值电场|电场峰值',question) and '平面波' in question and
            re.search(r'相同|一样|相等',question) and re.search(r'能流|光强',question)):
        return None
    a,b = (re.search(rf'{label}[^。，；]{{0,18}}?({UNSIGNED})\s*(?:ω|ohm|欧姆)',question)
           for label in ('甲','乙'))
    if not a or not b or min(float(a[1]),float(b[1]))<=0:
        return None
    first,second=float(a[1]),float(b[1])
    larger='甲' if first<second else '乙'
    result=(f'{larger}的平均能流更大。' if first!=second else '两者的平均能流相等。')
    result+=f'甲的平均能流是乙的 {second/first:.6g} 倍。'
    return _record('plane-wave-impedance-comparison','同峰值电场的平均能流',
                   'I=abs(E_peak)²/(2η)',{'impedance_a_ohm':first,'impedance_b_ohm':second},result,
                   '单向均匀无损平面波，电场为同一口径的峰值。',
                   'field-amplitude-vs-intensity-material','em-average-poynting')


def _parametric_power_increment(question):
    if not (re.search(r'参量下转换|参量下变频|参量放大|三波.{0,5}(?:过程|混频|交换)',question)
            and '无损' in question and '闲频' in question):
        return None
    pump,signal = (re.search(rf'{label}(?:浦|光)?(?:频率)?\s*(?:为|是|[:=])?\s*({UNSIGNED})\s*thz',question)
                   for label in ('泵','信号'))
    power = re.search(rf'信号(?:功率)?(?:增加|新增|增大)\s*({UNSIGNED})\s*(fw|pw|nw|μw|uw|mw|w)(?![a-z])',question)
    if not pump or not signal or not power:
        return None
    nu_p,nu_s=float(pump[1]),float(signal[1])
    if not 0<nu_s<nu_p:
        return None
    nu_i=nu_p-nu_s
    increment=float(power[1])*WATTS[power[2]]
    return _record('parametric-output-power','参量下转换的闲频功率',
                   'ν_i=ν_p−ν_s，ΔP_i=ΔP_s ν_i/ν_s',
                   {'pump_thz':nu_p,'signal_thz':nu_s,'signal_increment_w':increment},
                   f'闲频 {nu_i:g} THz，闲频功率增加 {increment*nu_i/nu_s/1e-3:.6g} mW。',
                   '无损单三波过程，新增的两路光子通量相等。',
                   'nonlinear-manley-rowe-parametric','nonlinear-gain-signal-idler')


def _slit_scale_ratio(question):
    if not ('单缝' in question or '缝宽' in question) or not re.search(r'焦平面|远场',question):
        return None
    if not re.search(r'暗纹|全宽|中央亮区',question) or re.search(r'近场|不满足|并非远场',question):
        return None
    values={}
    expressions={}
    for key,label in (('wavelength','波长'),('distance','焦距|屏距'),('width','缝宽')):
        pattern=rf'(?:{label})\s*(?:调成|调为|变成|变为|改成|改为|是|为)?\s*(?:原来的?\s*)?'
        part=re.search(pattern+rf'({NUMBER})(?:\s*/\s*({NUMBER}))?\s*倍',question)
        fraction=re.search(pattern+r'([一二两三四五六七八九十])分之([一二两三四五六七八九十])',question)
        if part:
            if part[2] and not float(part[2]):
                return None
            values[key]=float(part[1])/(float(part[2]) if part[2] else 1)
            expressions[key]=part[1]+('/'+part[2] if part[2] else '')
        elif fraction:
            numbers=dict(zip('一二三四五六七八九十',range(1,11)))|{'两':2}
            values[key]=numbers[fraction[2]]/numbers[fraction[1]]
            expressions[key]=f'{numbers[fraction[2]]}/{numbers[fraction[1]]}'
        elif re.search(rf'(?:{label})\s*(?:不动|不变|相同|保持)',question):
            values[key]=1.
            expressions[key]='1'
        else:
            return None
    if not all(value>0 for value in values.values()):
        return None
    ratio=values['wavelength']*values['distance']/values['width']
    return _record('single-slit-relative-scale','单缝暗纹尺度比',
                   'x₁₂/x₁₁ = (λ₂/λ₁)(ℓ₂/ℓ₁)/(a₂/a₁)',
                   values,f'({expressions["wavelength"]}) × ({expressions["distance"]}) ÷ ({expressions["width"]}) = {ratio:.7g} 倍。'
                   + ('第一暗纹位置与中央全宽保持。' if math.isclose(ratio,1.,rel_tol=1e-10) else ''),
                   '理想薄透镜焦平面用 ℓ=f，自由空间远场用 ℓ=L；比例只对应横向尺度。',
                   'slit-width-definitions','slit-scale')


def _basis_measurement(question, prior):
    question = re.sub(r'(?:取消|不做|跳过)\s*[xz]\s*基(?:测量|操作)?','',question)
    if prior and prior['id']=='qubit-basis-measurement' and re.search(r'接着|然后|随后|继续|再测|第二次|下一次',question):
        return None  # A sequential measurement needs the post-state, not the original pure input.
    if not re.search(r'测量|忘掉|丢掉|结果',question) or not (
        re.search(r'\|[01+−-][⟩〉]',question) or prior and prior['id']=='qubit-basis-measurement'
    ):
        return None
    old = prior['inputs'] if prior and prior['id']=='qubit-basis-measurement' else {}
    inputs = re.findall(r'\|([01+−-])[⟩〉]',question)
    bases = re.findall(r'(?<![a-z])([xz])\s*基',question)
    if not bases:
        bases = re.findall(r'(?<![a-z])([xz])\s*的\s*(?:正|负|0|1)\s*结果',question)
    if '计算基' in question:
        bases.append('z')
    basis_set = set(bases)
    if len(set(inputs))>1 or len(basis_set)>1:
        return None
    state = (inputs[0] if inputs else old.get('state','')).replace('−','-')
    basis = next(iter(basis_set),old.get('basis'))
    if not state or not basis:
        return None
    states = '01' if basis=='z' else '+-'
    p = 1.0 if state==states[0] else 0.0 if state==states[1] else 0.5
    probabilities = (p,1-p)
    selected = re.search(r'(?:只|仅).{0,9}(?:看到|保留|记录|得到).{0,8}(正|负|0|1)(?:\s*结果|\s*分支|\s*$)',question)
    distribution='、'.join(f'{label} 概率 {weight:g}' for label,weight in zip(states,probabilities))
    if selected:
        index=0 if selected[1] in ('正','0') else 1
        text=(f'所选条件态是 |{states[index]}⟩。' if probabilities[index] else '该结果的概率为零，条件态无定义。')
    else:
        text='各非零结果对应各自的本征条件态。'
    unrecorded = ('I/2（最大混合态）' if p==0.5 else f'纯态 |{state}⟩⟨{state}|')
    return _record('qubit-basis-measurement','理想二能级投影测量',
                   'p_j=Tr(Π_jρ)，ρ′=Σ_j Π_jρΠ_j',
                   {'state':state,'basis':basis,'selected':selected[1] if selected else None},
                   f'{basis.upper()} 基：{distribution}。{text} 忽略结果时为 {unrecorded}。',
                   '输入为指定的理想单二能级纯态，采用所给 X 或 Z 基的理想投影测量。',
                   'quantum-measurement-post-state','quantum-projective-measurement')


def _spatial_frequency_to_wavevector(question):
    if not ('波矢' in question and re.search(r'空间频率|周期/',question)):
        return None
    m=re.search(rf'({UNSIGNED})\s*(?:周期|cycles?)\s*/\s*(mm|cm|m|μm|um)(?![a-z])',question)
    if not m:
        return None
    frequency,unit=float(m[1]),m[2]
    return _record('spatial-wavevector','空间频率与横向波矢换算','kₓ=2π fₓ',
                   {'cycles_per_length':frequency,'length_unit':unit},
                   f'{frequency:g} 周期/{unit} 对应 {2*math.pi*frequency:.6g} rad/{unit}。'
                   '前向 Fourier 变换核为 exp(−ikₓx)，逆变换的平面波基为 exp(+ikₓx)。',
                   '采用负号前向 Fourier 变换约定，保持坐标与波矢单位一致。',
                   'spatial-frequency','fourier-transform-pair','cycles-and-angular-frequency')


def _group_index(question, prior):
    old = prior['inputs'] if prior and prior['id']=='group-index-local' else {}
    if not (re.search(r'群折射率|群指数|群速度|群时延|群延迟|折射率.{0,6}斜率',question)
            or old and re.search(r'斜率|波长|有效折射率|折射率',question)):
        return None
    fields = (
        ('index', r'(?:有效折射率|n[_ ]?eff)', {'':1}),
        ('wavelength_nm', r'(?:真空波长|波长|λ[₀0]?)', {'nm':1,'μm':1000,'um':1000}),
        ('slope_per_nm', r'(?:局部斜率|斜率|dn[_ ]?eff\s*/\s*dλ[₀0]?)', {'nm':1,'μm':.001,'um':.001}),
    )
    join=r'\s*(?:改为|改成|换成|为|是|[=:：])?\s*'
    values = {}
    for key,label,scales in fields:
        unit='|'.join(scales)
        if key=='slope_per_nm':
            matches=re.findall(rf'{label}{join}([+−-]?{NUMBER})\s*(?:/\s*|每\s*)({unit})\b',question)
            matches += re.findall(rf'{label}{join}([+−-]?{NUMBER})\s*({unit})\s*[⁻−-]1\b',question)
        else:
            matches=re.findall(rf'{label}{join}({UNSIGNED})\s*({unit})(?![a-z0-9.])',question)
        numbers={float(n.replace('−','-'))*scales[u] for n,u in matches}
        if len(numbers)==1:
            values[key]=numbers.pop()
        elif not numbers and key in old and not re.search(rf'{label}\s*(?:改|换|未知|不确定)',question):
            values[key]=old[key]
        else:
            return None
    if not all(math.isfinite(v) for v in values.values()) or values['wavelength_nm']<=0 or values['index']<=0:
        return None
    index, wavelength, slope = values['index'], values['wavelength_nm'], values['slope_per_nm']
    ng=index-wavelength*slope
    if ng<=0:
        return None
    return _record('group-index-local','群折射率与群速度','n_g = n_eff − λ₀ dn_eff/dλ₀；v_g = c/n_g',
                   values,f'n_g = {index:g} − ({wavelength:g} nm) × ({slope:g} /nm) = {ng:.7g}；v_g ≈ {C/ng:.7g} m/s。',
                   '同一波导结构与同一连续模式。折射率和局部导数须对应所给真空波长，采用窄带群速度。',
                   'group-index','group-velocity','group-velocity-dispersion')


def _pulse_time_shift(question, prior):
    old = prior['inputs'] if prior and prior['id']=='pulse-time-shift' else {}
    if not old and not re.search(r'谱相位|脉冲|时间记号|包络|时移',question):
        return None
    if not re.search(r'延迟|延后|推迟|提前|到达|时移|合成|逆变换',question):
        return None
    matches = re.findall(rf'(延迟|延后|推迟|提前|时移)\s*(?:了|为|改成|[=:：])?\s*({UNSIGNED})\s*(fs|ps|ns)\b',question)
    times = {(-1 if word=='提前' else 1)*float(number)*{'fs':1,'ps':1000,'ns':1e6}[unit]
             for word,number,unit in matches}
    if len(times)>1 or not times and (not old or re.search(r'(?:延迟|延后|提前)\s*(?:改|换|未知)',question)):
        return None
    time=next(iter(times),old.get('shift_fs'))
    signs=set(re.findall(r'(?:逆变换|合成)(?:核|因子)?\s*(?:改用|改成|换成|用|采用|是|为|[=:：])?\s*exp\s*\(\s*([+−-])\s*i',question))
    signs={sign.replace('−','-') for sign in signs}
    if len(signs)>1 or not signs and re.search(r'(?:换|改).{0,6}(?:傅里叶|合成|符号|约定)|(?:正|负)号约定',question):
        return None
    sign=next(iter(signs),old.get('inverse_sign','-'))
    slope=-time if sign=='+' else time
    physical='延后' if time>=0 else '提前'
    offset=f't−{time:g} fs' if time>=0 else f't+{abs(time):g} fs'
    return _record('pulse-time-shift','脉冲平移与谱相位斜率','A_out(t) = A_in(t−τ)，φ′ = −sτ（合成核 exp(siΩt)）',
                   {'shift_fs':time,'inverse_sign':sign},
                   f'到达时间{physical} {abs(time):g} fs，包络写作 A_in({offset})。合成核 exp({sign}iΩt) 配合传递 exp(+iφ) 时，线性谱相位斜率为 {slope:g} fs。',
                   '固定实验室时间轴，传递只加线性谱相位。移动坐标 T=t−β₁z 的原点变化需另行说明。',
                   'ultrafast-spectral-phase-constant-linear','ultrafast-retarded-time-coordinate','fourier-transform-pair')


def calculate(question, prior=None):
    text = canonical_notation(question).casefold().replace('µ','μ')
    if result := _detector_scale(text) or _fiber_acceptance(text, prior) or _fret_comparison(text):
        return result
    for operation in (_spectral_density,_detectivity,_waveplate,_camera,_purity,_mixing_wavelengths,
                      _conversion_mismatch_width,_gaussian_gdd,_mean_plane_wave_comparison,
                      _parametric_power_increment,_spatial_frequency_to_wavevector,_slit_scale_ratio):
        if result := operation(text):
            return result
    return (_basis_measurement(text,prior) or _slit(text,prior) or _nep(text,prior)
            or _group_index(text,prior) or _pulse_time_shift(text,prior) or _wavelength(text))


def _detector_scale(question):
    if not re.search(r'散粒|shot noise|相对噪声|相对波动|电流',question) or not re.search(r'带宽|散粒|shot noise',question):
        return None
    if re.search(r'带宽.{0,5}(?:也变|改为|减半|翻倍|同时变)|非泊松|散粒噪声以外',question):
        return None
    fraction = re.search(r'电流[^。；，,]{0,22}(?<![\d.])(1\s*/\s*4|四分之一)',question)
    ratio = re.search(r'(?:电流.{0,13}(?:原来(?:的)?|变为|变成|增为|增至|改为|乘以)\s*|(?<=原来的))('+NUMBER+r')\s*倍',question)
    k = .25 if fraction else float(ratio[1]) if ratio else 4 if re.search(r'(?:电流.{0,8})四倍',question) else None
    if not k or not math.isfinite(k):
        return None
    root = math.sqrt(k)
    return _record('shot-noise-scale','散粒噪声的电流缩放','iᵣₘₛ = √(2qIB)，相对噪声 = iᵣₘₛ/I，信噪幅度比 = I/iᵣₘₛ',
                   {'current_ratio':k},
                   f'电流变为原来的 {k:g} 倍时，绝对噪声为 {root:.6g} 倍，相对噪声为 {1/root:.6g} 倍，信号与噪声幅度之比为 {root:.6g} 倍。',
                   '固定等效噪声带宽、独立泊松载流子；只比较散粒噪声。',
                   'photodiode-shot-noise','photon-count-poisson')


def _fiber_acceptance(question, prior):
    old = prior['inputs'] if prior and prior['id']=='fiber-acceptance-angle' else {}
    if not (re.search(r'阶跃|纤芯|包层|数值孔径|接受半角',question)
            or old and re.search(r'外部|外界|浸|沿用|空气.*换',question)):
        return None
    if re.search(r'不是阶跃|渐变折射率|单模|外界未知|外部未知',question):
        return None
    fields = {'core_index':r'(?:纤芯|芯层|芯)(?:折射率)?','clad_index':r'包层(?:折射率)?',
              'outside_index':r'(?:外部|外界|入射侧|空气)(?:介质)?(?:折射率)?'}
    values = {}
    for name, label in fields.items():
        found = set(re.findall(label+r'\s*(?:从空气)?\s*(?:换为|换成|变成|设为|改为|改成|等于|是|为|取|[:=])?\s*('+UNSIGNED+r')',question))
        if len(found)>1:
            return None
        if found:
            values[name] = float(found.pop())
        elif name=='outside_index' and re.search(r'空气(?:中|里|作入射|入射)|外(?:部|界).{0,5}空气',question):
            values[name] = 1.0
        elif name in old and not re.search(label+r'.{0,4}(?:改|换|未知|取消)',question):
            values[name] = old[name]
        else:
            return None
    core, clad, out = (values[name] for name in fields)
    if not 0 < clad < core or out <= 0:
        return None
    na = math.sqrt(core**2-clad**2)
    if na > out:
        return None
    angle = math.degrees(math.asin(na/out))
    return _record('fiber-acceptance-angle','阶跃多模光纤的接受半角','NA = √(n_core² − n_clad²)，θ_max = arcsin(NA/n_ext)',
                   values, f'纤芯 {core:g}、包层 {clad:g}、外界 {out:g}：NA ≈ {na:.6g}，外界接受半角 ≈ {angle:.5g}°。',
                   '直阶跃多模光纤，平端面、子午光线模型，角度相对于光纤轴。',
                   'fiber-numerical-aperture','fiber-acceptance-angle')


def _fret_comparison(question):
    if not re.search(r'归一(?:化)?距离|r\s*/\s*r[0₀]',question):
        return None
    pair=re.search(r'从\s*('+UNSIGNED+r')\s*(?:改|换|增|减|回)?到\s*('+UNSIGNED+r')',question)
    if not pair:
        return None
    a,b=map(float,pair.groups())
    if not .01<=a<=100 or not .01<=b<=100:
        return None
    first,second=(1/(1+x**6) for x in (a,b))
    return _record('fret-efficiency','FRET 两个距离的效率','E = 1/[1+(r/R₀)^6]',
                   {'ratio_a':a,'ratio_b':b},
                   f'距离比 {a:g} 时效率 {first:.2%}，距离比 {b:g} 时效率 {second:.2%}。第二个相对第一个变化 {(second-first)*100:+.2f} 个百分点，相对变化 {(second-first)/first:+.2%}。',
                   '弱耦合点偶极模型，供体、受体、取向和环境固定。',
                   'forster-energy-transfer')


def contextual_calculation(question, user_turns):
    """Complete registered formula inputs from user words, never AI prose."""
    words=[unicodedata.normalize('NFKC',q).casefold().replace('µ','μ') for q in user_turns]+[
        unicodedata.normalize('NFKC',question).casefold().replace('µ','μ')]
    current=words[-1]
    if any(re.search(r'光纤|纤芯|阶跃',q) for q in words[:-1]):
        fiber = None
        for text in words[:-1]:
            if re.search(r'不是阶跃|渐变折射率|单模|外界未知|外部未知',text):
                fiber = None
                continue
            fiber = _fiber_acceptance(text,fiber) or fiber
        if fiber and re.search(r'接受半角|外部|外界|空气.*换|浸',current):
            result = _fiber_acceptance(current,fiber)
            if result:
                return result
    if any(re.search(r'fret|r\s*/\s*r0|归一(?:化)?距离',q) for q in words[:-1]):
        if re.search(r'反过来|回到|顺序',current):
            result=_fret_comparison('r/r0 '+current)
            if result:
                return result
    if not re.search(r'带宽|nep|p[wW]|等效功率|更小|比较|核算|多少',current):
        return None
    devices={};active=0
    for text in words:
        if re.search(r'不是白噪声|非白噪声|谱不平坦',text):
            return None
        ds=_positive(NEP.findall(text),WATTS)
        bs=_positive(BANDWIDTH.findall(text),HERTZ)
        named=re.search(r'第\s*([一二12])\s*(?:台|路|个)|(?<![a-z])([ab])\s*(?:探测器|设备|通道|台|带宽)',text)
        if len(ds)>1 or len(bs)>1:
            return None  # The existing complete-expression calculator owns this case.
        if named:active=0 if (named[1] or named[2]) in ('一','1','a') else 1
        if ds:
            devices.setdefault(active,{})['nep_w_per_sqrt_hz']=ds[0]
        if bs and (active in devices or named):
            devices.setdefault(active,{})['bandwidth_hz']=bs[0]
    if not devices or sorted(devices)!=list(range(len(devices))) or any(len(d)!=2 for d in devices.values()):
        return None
    pairs=[devices[i] for i in sorted(devices)]
    text='；'.join(f'第 {i} 台：{d["nep_w_per_sqrt_hz"]/1e-12:.7g} pW/√Hz × √({d["bandwidth_hz"]:.7g} Hz) = {d["nep_w_per_sqrt_hz"]*math.sqrt(d["bandwidth_hz"])/1e-12:.7g} pW' for i,d in enumerate(pairs,1))
    return _record('nep-bandwidth','等效输入均方根噪声','P_rms = NEP_s √B',pairs,text,
                   '沿用用户逐轮明确给出的各设备条件；等效噪声带宽与白噪声近似。',
                   'detector-nep','detector-noise-bandwidth')
