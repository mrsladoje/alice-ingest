"""Mask the varying parts of a log line, twice: once readably, once quickly.

`REFERENCE` is the rule list as `drain3` consumes it, and `reference_mask` runs
it the way `drain3` does — seven `re.sub` passes in order. It is the definition
of correct. `mask` is a rewrite that produces the same string 85 to 87 % faster,
and round 6 measured the whole templating cost falling 2.3 times because of it.

The rewrite is a set of local identities on the regexes, not a new algorithm.
`((?<=[^A-Za-z0-9])|^)` is the same predicate as `(?<![A-Za-z0-9])`, and `\\b`
before a word character is the same as `(?<!\\w)`. The change that pays is moving
each pattern's first literal in front of its assertion: `re` uses its
character-skip loop only when the first opcode is a literal or a class, so a
pattern opening with an assertion runs the full matcher at every position of
every line. The lookbehind then checks one character further back and the match
set is unchanged.

Three more mechanisms stack on that. A `str.__contains__` gate in front of each
rule, since a rule that cannot match should not be scanned for. An ASCII fast
path, because `\\d` and `\\w` are Unicode-aware and `[0-9]` and `[A-Za-z0-9_]`
compile to a bitmap test; lines that are not ASCII fall back to Unicode-exact
patterns. And FLOAT and NUM folded into one scan, without a per-match callback,
which measured slower than the scans it saved.

`_numbers` reproduces a quirk of the two-pass reference rather than correcting
it. FLOAT runs over the whole line before NUM does, so in `1.5-3` the second
pass swallows the sign and the answer is `<FLOAT><NUM>`, not `<FLOAT>-<NUM>`.
Byte-identical output is the whole requirement here: every template identifier
downstream is derived from this string, so a faster masker that improved the
quirk would silently renumber the corpus.

Equivalence was checked on all 3,000,000 lines of each family, on the stripped
input the recipe actually feeds it, on 3,000,000 random strings over an alphabet
carrying Arabic-Indic digits and combining accents, and on the mined templates
themselves — set and per-template line count both identical.
"""
import re

REFERENCE = [
    {"regex_pattern": r"((?<=[^A-Za-z0-9])|^)(/[-\w./]+)((?=[^A-Za-z0-9])|$)", "mask_with": "PATH"},
    {"regex_pattern": r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "mask_with": "UUID"},
    {"regex_pattern": r"\b(\d{1,3}\.){3}\d{1,3}(:\d+)?\b", "mask_with": "IP"},
    {"regex_pattern": r"\b0[xX][0-9a-fA-F]+\b", "mask_with": "HEX"},
    {"regex_pattern": r"\b\d{1,3}(,\d{3})+\b", "mask_with": "NUM"},
    {"regex_pattern": r"((?<=[^A-Za-z0-9])|^)([\-\+]?\d+\.\d+)((?=[^A-Za-z0-9])|$)", "mask_with": "FLOAT"},
    {"regex_pattern": r"((?<=[^A-Za-z0-9])|^)([\-\+]?\d+)((?=[^A-Za-z0-9])|$)", "mask_with": "NUM"},
]

_PATH = re.compile(r"/(?<![A-Za-z0-9]/)[-A-Za-z0-9_./]+(?![A-Za-z0-9])").sub
_UUID = re.compile(r"(?<![A-Za-z0-9_])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![A-Za-z0-9_])").sub
_UUID_HINT = re.compile(r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-").search
_IP = re.compile(r"[0-9](?<![A-Za-z0-9_][0-9])[0-9]{0,2}\.(?:[0-9]{1,3}\.){2}[0-9]{1,3}(?::[0-9]+)?(?![A-Za-z0-9_])").sub
_IP_HINT = re.compile(r"\.[0-9]{1,3}\.[0-9]{1,3}\.").search
_HEX = re.compile(r"0(?<![A-Za-z0-9_]0)[xX][0-9a-fA-F]+(?![A-Za-z0-9_])").sub
_GROUPED = re.compile(r"[0-9](?<![A-Za-z0-9_][0-9])[0-9]{0,2}(?:,[0-9]{3})+(?![A-Za-z0-9_])").sub
_GROUPED_HINT = re.compile(r",[0-9]{3}").search
_NUM = re.compile(r"[-+0-9](?<![A-Za-z0-9].)(?:(?<=[-+])[0-9]|(?<=[0-9]))[0-9]*(?![A-Za-z0-9])").sub
_NUMBER_TOKENS = re.compile(r"([-+0-9](?<![A-Za-z0-9].)(?:(?<=[-+])[0-9]|(?<=[0-9]))[0-9]*(?:\.[0-9]+)?(?![A-Za-z0-9]))").split

_U_PATH = re.compile(r"/(?<![A-Za-z0-9]/)[-\w./]+(?![A-Za-z0-9])").sub
_U_UUID = re.compile(r"[0-9a-fA-F](?<!\w[0-9a-fA-F])[0-9a-fA-F]{7}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?!\w)").sub
_U_IP = re.compile(r"\d(?<!\w\d)\d{0,2}\.(?:\d{1,3}\.){2}\d{1,3}(?::\d+)?(?!\w)").sub
_U_IP_HINT = re.compile(r"\.\d{1,3}\.\d{1,3}\.").search
_U_HEX = re.compile(r"0(?<!\w0)[xX][0-9a-fA-F]+(?!\w)").sub
_U_GROUPED = re.compile(r"\d(?<!\w\d)\d{0,2}(?:,\d{3})+(?!\w)").sub
_U_GROUPED_HINT = re.compile(r",\d{3}").search
_U_FLOAT = re.compile(r"[-+\d](?<![A-Za-z0-9].)(?:(?<=[-+])\d|(?<=\d))\d*\.\d+(?![A-Za-z0-9])").sub
_U_NUM = re.compile(r"[-+\d](?<![A-Za-z0-9].)(?:(?<=[-+])\d|(?<=\d))\d*(?![A-Za-z0-9])").sub


def _numbers(line):
    parts = _NUMBER_TOKENS(line)
    if len(parts) == 1:
        return line
    out = []
    after_float = False
    for i in range(0, len(parts) - 1, 2):
        text = parts[i]
        token = parts[i + 1]
        if "." in token:
            out.append(text)
            out.append("<FLOAT>")
            after_float = True
        else:
            if not (after_float and (text == "-" or text == "+")
                    and token[0] != "-" and token[0] != "+"):
                out.append(text)
            out.append("<NUM>")
            after_float = False
    out.append(parts[-1])
    return "".join(out)


def _mask_unicode(line):
    if "/" in line:
        line = _U_PATH("<PATH>", line)
    if _UUID_HINT(line):
        line = _U_UUID("<UUID>", line)
    if _U_IP_HINT(line):
        line = _U_IP("<IP>", line)
    if "0x" in line or "0X" in line:
        line = _U_HEX("<HEX>", line)
    if _U_GROUPED_HINT(line):
        line = _U_GROUPED("<NUM>", line)
    if "." in line:
        line = _U_FLOAT("<FLOAT>", line)
    return _U_NUM("<NUM>", line)


def mask(line):
    if not line.isascii():
        return _mask_unicode(line)
    if "/" in line:
        line = _PATH("<PATH>", line)
    if "-" in line and _UUID_HINT(line):
        line = _UUID("<UUID>", line)
    if "." in line and _IP_HINT(line):
        line = _IP("<IP>", line)
    if "0x" in line or "0X" in line:
        line = _HEX("<HEX>", line)
    if "," in line and _GROUPED_HINT(line):
        line = _GROUPED("<NUM>", line)
    if "." in line:
        return _numbers(line)
    return _NUM("<NUM>", line)


def reference_masker():
    from drain3.masking import LogMasker, MaskingInstruction
    return LogMasker(
        [MaskingInstruction(m["regex_pattern"], m["mask_with"]) for m in REFERENCE],
        "<", ">")


def reference_mask(line):
    global _REFERENCE_MASKER
    try:
        return _REFERENCE_MASKER.mask(line)
    except NameError:
        _REFERENCE_MASKER = reference_masker()
        return _REFERENCE_MASKER.mask(line)
