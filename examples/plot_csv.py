"""Create three separate SVG charts for 2231A CSV log. Usage: python examples/plot_csv.py run.csv"""
import csv
from datetime import datetime
from pathlib import Path
import sys
import matplotlib.pyplot as plt

path = Path(sys.argv[1])
rows = list(csv.DictReader(path.open(encoding='utf-8', newline='')))
if not rows: raise SystemExit('empty CSV')
for col, ylabel in (('voltage_v', 'Voltage (V)'), ('current_a', 'Current (A)'),
                    ('power_w', 'Estimated power (W)')):
    plt.figure(figsize=(10, 5))
    for label in sorted(set(r['label'] for r in rows)):
        data = [r for r in rows if r['label'] == label]
        times = [datetime.fromisoformat(r['timestamp_utc']) for r in data]
        values = [float(r[col]) for r in data]
        plt.plot(times, values, label=label)
    plt.xlabel('UTC time'); plt.ylabel(ylabel)
    plt.legend(); plt.grid(True, alpha=.2)
    plt.tight_layout()
    output = path.with_name(f'{path.stem}_{col}.svg')
    plt.savefig(output)
    plt.close()
    print(output)
