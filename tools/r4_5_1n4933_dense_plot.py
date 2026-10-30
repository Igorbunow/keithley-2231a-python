#!/usr/bin/env python3
"""Offline 1N4933 dense forward IV plot and integrity/shape diagnostics.

No transport imports or instrument I/O. Always diagnostic, never metrology PASS.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape


def review(directory: Path):
    manifest=json.loads((directory/'completed.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'EXAMPLE_COMPLETED_NOT_INDEPENDENT_HW_QUALIFICATION':
        raise ValueError('Not a complete guarded example')
    channels=manifest.get('channels')
    if type(channels) is not list or len(channels)!=1 or channels[0].get('channel')!=1:
        raise ValueError('Expected one CH1 series')
    info=channels[0]
    if (info.get('current_source')!='EXTERNAL_DIODE_DCV_BALLAST' or
            info.get('force') or not info.get('digital_off_confirmed') or
            not info.get('physical_off_operator_confirmed') or
            not info.get('external_voltage_used') or info.get('point_count')!=61):
        raise ValueError('Missing verified OFF/diode DCV provenance')
    csv_file=directory/info['csv']
    raw=csv_file.read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if info.get('csv_sha256')!=digest:
        raise ValueError('CSV SHA-256 mismatch')
    with csv_file.open(newline='',encoding='utf-8') as f:
        rows=list(csv.DictReader(f))
    if len(rows)!=61:
        raise ValueError('Expected exactly 61 samples')
    data=[]
    for n,row in enumerate(rows):
        if row['source'] != 'EXTERNAL_DIODE_DCV_BALLAST' or row['regulation']!='CV' or row['accuracy_qualified']!='False':
            raise ValueError('Unexpected point source/CV/status')
        if int(row['index'])!=n or row['forced_low_accuracy']!='False':
            raise ValueError('Malformed index or force flag')
        values=tuple(float(row[key]) for key in ('requested_psu_v','measured_psu_v',
                        'selected_current_a','psu_readback_a','measured_diode_dcv_v'))
        if any(not math.isfinite(v) for v in values):
            raise ValueError('Nonfinite point')
        set_v, src_v, current, _psu_i, diode_v=values
        expected=round(.60+.02*n,2) if n<=30 else round(1.20-.02*(n-30),2)
        if abs(set_v-expected)>1e-8 or not 0<=diode_v<=src_v<=1.26:
            raise ValueError('Point out of reviewed 61-point profile envelope')
        if not 0<=current<=.022 or abs(current-(src_v-diode_v)/99.86)>1e-9:
            raise ValueError('Invalid ballast-current computation')
        data.append((diode_v,current*1000,set_v))
    up=data[:31]
    down=data[31:]
    pair_deltas=[abs(up[i][1] - data[60-i][1]) for i in range(30)]
    rising_violations=sum(1 for a,b in zip(up,up[1:]) if b[1] + .25 < a[1])
    max_delta=max(pair_deltas)
    result={
        'status':'OFFLINE_DIAGNOSTIC_REVIEW_NOT_CALIBRATED',
        'csv_sha256':digest,'n_points':61,'n_forward':31,'n_return':30,
        'min_diode_v':min(x[0] for x in data),'max_diode_v':max(x[0] for x in data),
        'min_ballast_current_ma':min(x[1] for x in data),
        'max_ballast_current_ma':max(x[1] for x in data),
        'return_current_max_abs_difference_ma':max_delta,
        'monotonicity_violations_over_0p25ma':rising_violations,
        'shape_review':'REVIEW_REQUIRED' if max_delta>.45 or rising_violations else 'NO_LARGE_ANOMALIES_DETECTED',
        'limitations':[
            'PSU voltage readback nominal +/- (0.06% + 20mV) at 25 +/-5 C',
            '20mV PSU voltage offset alone corresponds to roughly 0.20mA at 99.86ohm',
            'DMM and PSU readings sampled sequentially, not simultaneously',
            'Resistor and DMM uncertainty, lead drops, temperature not combined',
            'Only forward-biased diode tested; no reverse I-V or absolute calibration',
        ]}
    return data,result


def plot_svg(data, filename):
    w,h=900,550
    left,right,top,bottom=86,30,45,70
    vw=w-left-right; vh=h-top-bottom
    xmax=max(.7,max(v for v,_,_ in data)*1.06)
    ymax=max(.5,max(i for _,i,_ in data)*1.12)
    def x(v):return left+vw*v/xmax
    def y(i):return top+vh-vh*i/ymax
    lines=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="Diagnostic 1N4933 forward I-V curve">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<text x="450" y="26" text-anchor="middle" font-size="18" fill="#111827">1N4933 forward I-V (diagnostic only)</text>']
    for n in range(6):
        yy=top+vh*n/5
        val=ymax*(1-n/5)
        lines.append(f'<path d="M{left},{yy:.1f} H{w-right}" stroke="#e5e7eb"/>')
        lines.append(f'<text x="{left-12}" y="{yy+4:.1f}" text-anchor="end" fill="#374151" font-size="12">{val:.2f}</text>')
    for n in range(7):
        xx=left+vw*n/6
        val=xmax*n/6
        lines.append(f'<path d="M{xx:.1f},{top} V{h-bottom}" stroke="#f3f4f6"/>')
        lines.append(f'<text x="{xx:.1f}" y="{h-bottom+19}" text-anchor="middle" fill="#374151" font-size="12">{val:.2f}</text>')
    lines.append(f'<path d="M{left},{top} V{h-bottom} H{w-right}" stroke="#374151" stroke-width="1.5" fill="none"/>')
    for segment, color in ((data[:31],'#2563eb'),(data[30:],'#e11d48')):
        coords=' '.join(f'{x(v):.2f},{y(i):.2f}' for v,i,_ in segment)
        lines.append(f'<polyline points="{coords}" stroke="{color}" fill="none" stroke-width="2.5"/>')
        for v,i,_ in segment:
            lines.append(f'<circle cx="{x(v):.2f}" cy="{y(i):.2f}" r="2" fill="{color}"/>')
    lines += [f'<text x="{w/2:.1f}" y="{h-21}" text-anchor="middle" font-size="15" fill="#111827">Direct diode voltage DMM7510 (V)</text>',
       '<text transform="translate(21,275) rotate(-90)" text-anchor="middle" font-size="15" fill="#111827">Calculated ballast current (mA)</text>',
       '<text x="580" y="62" fill="#2563eb" font-size="12">Ascending 0.60 to 1.20 V</text>',
       '<text x="580" y="80" fill="#e11d48" font-size="12">Descending 1.18 to 0.60 V</text>',
       '</svg>']
    filename.write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    p.add_argument('--out',type=Path,help='Fresh directory for SVG + diagnostics JSON')
    args=p.parse_args(argv)
    try:
        points,result=review(args.directory)
        if args.out:
            if args.out.exists():
                raise ValueError('Output directory exists; no overwrite')
            args.out.mkdir(parents=False,exist_ok=False)
            plot_svg(points,args.out/'diode_iv.svg')
            (args.out/'diagnostics.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        p.error(str(exc))

if __name__=='__main__':raise SystemExit(main())
