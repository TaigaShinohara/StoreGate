#!/usr/bin/env python3
"""Read simple Android application target SDKs without evaluating Gradle.

Input is the guard's already filtered source list. Output is tab-separated
resolved/value/location or unknown/empty/reason. Libraries are never app targets.
Expressions, convention plugins and ambiguous variant configuration need a build
artifact or human verification; they must not become a successful static check.
"""
import re
import sys
from pathlib import Path


TOKEN = re.compile(
    r'//[^\n]*|/\*[\s\S]*?\*/|"""[\s\S]*?"""|\x27\x27\x27[\s\S]*?\x27\x27\x27'
    r'|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27'
    r'|[A-Za-z_$][\w$]*|[0-9]+|[^\S\n]+|\n|.', re.UNICODE)


def tokens(source):
    out, line = [], 1
    for match in TOKEN.finditer(source):
        word = match.group()
        if not word.startswith(('//', '/*')) and (word == '\n' or not word.isspace()):
            out.append((word, line))
        line += word.count('\n')
    return out


def record(kind, value, reason):
    reason = reason.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
    print(f'{kind}\t{value}\t{reason}')


def block_name(header):
    words = [t[0] for t in header if t[0] != '\n']
    if not words:
        return '?'
    if words[-1] == ')':
        joined = ''.join(words)
        named = re.fullmatch(r'(?:create|maybeCreate|getByName|named)\(["\x27]([^"\x27]+)["\x27]\)', joined)
        return named.group(1) if named else '?'
    return words[-1] if re.fullmatch(r'[A-Za-z_]\w*', words[-1]) else '?'


def literal(words):
    while len(words) >= 2 and words[0] == '(' and words[-1] == ')':
        words = words[1:-1]
    value = ''.join(words)
    if re.fullmatch(r'["\x27]?[0-9]+["\x27]?', value):
        return int(value.strip('"\x27'))
    return None


def analyze(path):
    ts = tokens(path.read_text(encoding='utf-8'))
    compact = ' '.join(t[0] for t in ts)
    # Groovy slashy and dollar-slashy strings can contain entire fake Android
    # blocks and plugin declarations. Unsupported slash syntax is never code evidence.
    if any(t[0] == '/' for t in ts):
        record('unknown', '', f'{path}: unquoted slash syntax (including Groovy slashy strings) requires resolved release configuration')
        return 'unknown'
    words = [t[0] for t in ts]
    # Applied scripts and dynamic setters can override a literal declared here.
    # Follow no Gradle code or network dependencies; require resolved evidence.
    external_apply = any(word == 'apply' and words[index + 1:index + 2] not in (['plugin'], ['false'])
                         for index, word in enumerate(words))
    dynamic_setter = any(word in ('setTargetSdk', 'setTargetSdkVersion', '"targetSdk"', "'targetSdk'",
                                 '"targetSdkVersion"', "'targetSdkVersion'") for word in words)
    if external_apply or dynamic_setter:
        record('unknown', '', f'{path}: applied scripts or dynamic target SDK setters require resolved release configuration')
        return 'unknown'
    if 'alias' in words:
        record('unknown', '', f'{path}: plugin catalog aliases require resolved application and release target configuration')
        return 'unknown'
    kinds, declarations, other_plugins = set(), 0, set()
    for index, word in enumerate(words):
        end, kind = index + 1, None
        if word == 'id':
            if end < len(words) and words[end] == '(':
                end += 1
            if end < len(words):
                plugin = re.fullmatch(r'["\x27]com\.android\.(application|library)["\x27]', words[end])
                if plugin:
                    kind = plugin.group(1)
                    end += 1
                    if end < len(words) and words[end] == ')':
                        end += 1
                elif re.fullmatch(r'["\x27][^"\x27]+["\x27]', words[end]):
                    plugin_id = words[end].strip('"\x27')
                    if plugin_id not in ('org.jetbrains.kotlin.android', 'org.jetbrains.kotlin.multiplatform',
                                         'kotlin-android', 'kotlin-multiplatform'):
                        other_plugins.add(plugin_id)
                else:
                    other_plugins.add('dynamic plugin id')
        elif words[index:index + 3] == ['apply', 'plugin', ':'] and index + 3 < len(words):
            plugin = re.fullmatch(r'["\x27]com\.android\.(application|library)["\x27]', words[index + 3])
            if plugin:
                kind, end = plugin.group(1), index + 4
            elif re.fullmatch(r'["\x27][^"\x27]+["\x27]', words[index + 3]):
                plugin_id = words[index + 3].strip('"\x27')
                if plugin_id not in ('org.jetbrains.kotlin.android', 'org.jetbrains.kotlin.multiplatform',
                                     'kotlin-android', 'kotlin-multiplatform'):
                    other_plugins.add(plugin_id)
            else:
                other_plugins.add('dynamic applied plugin')
        if not kind:
            continue
        declarations += 1
        stop = end
        while stop < len(words) and words[stop] not in ('\n', '{', '}', ';'):
            stop += 1
        # A version or alias declared with apply false is a root plugin catalog.
        tail = words[end:stop]
        if any(tail[j:j + 2] == ['apply', 'false'] for j in range(len(tail))):
            continue
        kinds.add(kind)
    if declarations and not kinds and not re.search(r'\bandroid\s*\{', compact):
        return 'none'
    if kinds and other_plugins:
        record('unknown', '', f'{path}: additional plugin effects are unresolved: {", ".join(sorted(other_plugins))}')
        return 'unknown'
    if kinds == {'library'}:
        return 'library'
    has_android = any(t[0] == 'android' for t in ts)
    if kinds != {'application'}:
        if has_android:
            record('unknown', '', f'{path}: cannot identify an Android application module (plugin or convention configuration)')
        return 'unknown' if has_android else 'none'

    stack, header, defaults, flavors, ambiguous = [], [], [], {}, []
    index = 0
    while index < len(ts):
        word, line = ts[index]
        if word == '{':
            name = block_name(header)
            stack.append(name)
            if len(stack) >= 2 and stack[-2] == 'productFlavors':
                flavors.setdefault(name, [])
            header = []
        elif word == '}':
            if stack:
                stack.pop()
            header = []
        elif word in (';', '\n'):
            # A multi-line named(...) header is retained until its brace.
            if word == ';' or not header or header[-1][0] != ')':
                header = []
        elif word in ('targetSdk', 'targetSdkVersion'):
            previous = ts[index - 1][0] if index else ''
            if previous in ('val', 'var', 'def'):
                header.append(ts[index])
                index += 1
                continue
            end, depth, expression = index + 1, 0, []
            if end < len(ts) and ts[end][0] == '=':
                end += 1
            while end < len(ts):
                part = ts[end][0]
                if depth == 0 and part in (';', '}', '{', '\n'):
                    break
                expression.append(part)
                depth += (part == '(') - (part == ')')
                end += 1
            # Explicit debug-only configuration cannot establish the release SDK.
            if 'buildTypes' in stack and 'debug' in stack[stack.index('buildTypes') + 1:]:
                index = end
                continue
            location = f'{path}:{line}'
            value = literal(expression)
            if stack == ['android', 'defaultConfig'] and previous != '.':
                defaults.append((value, location))
            elif len(stack) == 3 and stack[:2] == ['android', 'productFlavors'] and previous != '.':
                flavors.setdefault(stack[-1], []).append((value, location))
            else:
                ambiguous.append(f'{location}: target SDK is outside a simple defaultConfig or product flavor')
            index = end
            continue
        else:
            header.append(ts[index])
        index += 1

    for reason in ambiguous:
        record('unknown', '', reason)
    # Multiple assignments or multiple flavor dimensions require actual Gradle evaluation.
    dimensions = re.search(r'\bflavorDimensions\b([^\n{};]*)', compact)
    multi_dimensions = dimensions and len(re.findall(r'["\x27][^"\x27]+["\x27]', dimensions.group(1))) > 1
    if multi_dimensions:
        record('unknown', '', f'{path}: multiple flavor dimensions require resolved release variant SDKs')
        return 'app'
    variants = flavors or {'default': []}
    for flavor, overrides in variants.items():
        values = overrides or defaults
        label = f'{path}: release variant {flavor}'
        if ambiguous or flavor == '?' or len(values) != 1 or values[0][0] is None:
            record('unknown', '', f'{label}: target SDK is missing, an expression, or has ambiguous assignments')
        else:
            record('resolved', values[0][0], f'{values[0][1]}: release variant {flavor}')
    return 'app'


def main():
    if len(sys.argv) != 2:
        raise ValueError('usage: android-target-sdk.py <guard-file-list>')
    paths = [Path(p) for p in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines()]
    modules = {}
    for path in paths:
        if path.name not in ('build.gradle', 'build.gradle.kts'):
            continue
        try:
            modules[path.parent] = analyze(path)
        except (OSError, UnicodeError) as exc:
            record('unknown', '', f'{path}: cannot read Gradle configuration ({exc})')
    for path in paths:
        if path.name != 'AndroidManifest.xml' or re.search(r'/src/(?:debug|androidTest|test)/', path.as_posix()):
            continue
        module = next((modules[parent] for parent in path.parents if parent in modules), None)
        if module in (None, 'none'):
            record('unknown', '', f'{path}: no application build configuration supplies this manifest\'s release target SDK')
    if 'app' not in modules.values():
        record('unknown', '', 'No statically identifiable Android application target SDK; inspect the resolved release artifact or export native project configuration')


if __name__ == '__main__':
    main()
