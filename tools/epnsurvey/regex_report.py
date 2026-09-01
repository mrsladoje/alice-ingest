#!/usr/bin/env python3
import argparse
import json
import pathlib
import re
import sys

PARSER_BLOCK = re.compile(r'\[PARSER\](.*?)(?=\[PARSER\]|\Z)', re.S)
FIELD = re.compile(r'^\s*(Name|Regex|Format|Time_Key|Time_Format)\s+(.*?)\s*$', re.M)
NAMED_GROUP = re.compile(r'\(\?<([A-Za-z_][A-Za-z0-9_]*)>')


def load_parsers(path):
    out = []
    text = pathlib.Path(path).read_text()
    for block in PARSER_BLOCK.findall(text):
        fields = dict(FIELD.findall(block))
        name, pattern = fields.get('Name'), fields.get('Regex')
        if not name or not pattern:
            continue
        onig = NAMED_GROUP.sub(lambda m: '(?P<%s>' % m.group(1), pattern)
        try:
            compiled = re.compile(onig)
        except re.error as exc:
            out.append({'name': name, 'error': str(exc), 'regex': None,
                        'time_format': fields.get('Time_Format')})
            continue
        out.append({'name': name, 'regex': compiled, 'error': None,
                    'time_format': fields.get('Time_Format'),
                    'fields': sorted(compiled.groupindex)})
    return out


def read_lines(path, limit):
    lines = []
    with open(path, 'r', errors='replace') as handle:
        for i, line in enumerate(handle):
            if i >= limit:
                break
            lines.append(line.rstrip('\n'))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bundle')
    ap.add_argument('--parsers', required=True)
    ap.add_argument('--limit', type=int, default=200000)
    ap.add_argument('--json', default=None)
    args = ap.parse_args()

    parsers = load_parsers(args.parsers)
    broken = [p for p in parsers if p['error']]
    usable = [p for p in parsers if not p['error']]

    samples = sorted(pathlib.Path(args.bundle, 'samples').glob('jl__*.sample'))
    if not samples:
        sys.exit('no jl__*.sample files under %s/samples' % args.bundle)

    report = {'parsers_total': len(parsers), 'parsers_broken': len(broken),
              'broken': [{'name': p['name'], 'error': p['error']} for p in broken],
              'programs': {}, 'parser_totals': {p['name']: 0 for p in usable}}

    for sample in samples:
        stem = sample.name[len('jl__'):-len('.sample')]
        program, _, stream = stem.rpartition('__')
        lines = read_lines(sample, args.limit)
        counts = {}
        matched_any = 0
        for parser in usable:
            hits = sum(1 for line in lines if parser['regex'].search(line))
            if hits:
                counts[parser['name']] = hits
                report['parser_totals'][parser['name']] += hits
        for line in lines:
            if any(p['regex'].search(line) for p in usable):
                matched_any += 1
        report['programs'][f'{program} ({stream})'] = {
            'lines': len(lines),
            'lines_matched_by_any_parser': matched_any,
            'coverage_pct': round(100.0 * matched_any / len(lines), 1) if lines else 0.0,
            'per_parser': dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        }

    print('=' * 78)
    print('Thanasis parser library vs. real EPN job logs')
    print('=' * 78)
    print('parsers parsed: %d   uncompilable in Python re: %d'
          % (len(parsers), len(broken)))
    for b in broken:
        print('  BROKEN %-24s %s' % (b['name'], b['error']))
    print()
    print('%-42s %8s %8s %7s' % ('program (stream)', 'lines', 'matched', 'cover'))
    print('-' * 78)
    for name, data in sorted(report['programs'].items()):
        print('%-42s %8d %8d %6.1f%%'
              % (name, data['lines'], data['lines_matched_by_any_parser'],
                 data['coverage_pct']))
    print()
    print('%-28s %10s' % ('parser', 'total hits'))
    print('-' * 40)
    for name, total in sorted(report['parser_totals'].items(), key=lambda kv: -kv[1]):
        flag = '   <-- ZERO' if total == 0 else ''
        print('%-28s %10d%s' % (name, total, flag))
    print()
    print('per-program breakdown')
    print('-' * 78)
    for name, data in sorted(report['programs'].items()):
        top = ', '.join('%s=%d' % kv for kv in list(data['per_parser'].items())[:6])
        print('%-42s %s' % (name, top or '(nothing matched)'))

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
