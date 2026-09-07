import ast

import pytest

from analyzer import analyze_repository, guarded_call_facts, identity_return_facts
from interpretation import EvidencePacket, build_interpretation_input, known_evidence_refs

SOURCE = 'def f(value, store):\n    if value is None:\n        return False\n    store.save(value)\n    return True\n'


def test_guarded_call_claims_reach_model_input(tmp_path):
    (tmp_path / 'example.py').write_text(SOURCE)
    graph = analyze_repository(tmp_path)
    node = next(n for n in graph['nodes'] if n.get('name') == 'f')
    packet = EvidencePacket.model_validate(node['evidence_packet'])
    claims = [c for c in packet.claims if ':local-execution:' in (c.id or '')]
    assert len(claims) == 3
    assert all(c.classification == 'fact' and c.confidence == 1 for c in claims)
    assert all(c.evidence_refs == [node['id']] for c in claims)
    assert all(c.id in known_evidence_refs(packet) for c in claims)
    assert 'example.py:4-5' in claims[1].provenance
    payload = build_interpretation_input(packet, SOURCE)
    for text in ('result is ignored', 'only after normal completion', 'not establish operation success', 'exception handler'):
        assert text in payload


@pytest.mark.parametrize('source', [
    SOURCE.replace('is None', '== None'),
    SOURCE.replace('return False', 'return 0'),
    SOURCE.replace('return True', 'return 1'),
    SOURCE.replace('store.save(value)', 'result = store.save(value)'),
    SOURCE.replace('store.save(value)', 'return store.save(value)'),
    SOURCE.replace('def f', 'async def f'),
    SOURCE.replace('store.save(value)', 'store.save((yield value))'),
    'def f(value, store):\n    try:\n        store.save(value)\n    except Exception:\n        return False\n    return True\n',
    'def f(value, store):\n    with store:\n        store.save(value)\n    return True\n',
    SOURCE.replace('if value is None', 'if other is None'),
])
def test_unsupported_shapes_are_not_overclaimed(source):
    assert guarded_call_facts(ast.parse(source).body[0]) == []


def test_nested_body_not_attributed_to_outer_and_decorators_are_body_scoped(tmp_path):
    source = '@wrapper\n' + SOURCE
    source += '\ndef outer():\n' + ''.join('    ' + line + '\n' for line in SOURCE.splitlines())
    (tmp_path / 'example.py').write_text(source)
    nodes = analyze_repository(tmp_path)['nodes']
    outer = next(n for n in nodes if n.get('name') == 'outer')
    assert 'local_execution_facts' not in outer
    f = next(n for n in nodes if n.get('name') == 'f')
    assert 'function body' in f['local_execution_facts'][0]['text']


@pytest.mark.parametrize('source', [
    'def f(value): return value',
    'def f(value, /): return value',
    'def f(*, value): return value',
    '@wrapper\ndef f(value):\n    "doc"\n    return value',
])
def test_identity_facts_preserve_object_identity_and_body_scope(tmp_path, source):
    (tmp_path / 'example.py').write_text(source)
    node = next(n for n in analyze_repository(tmp_path)['nodes'] if n.get('name') == 'f')
    packet = EvidencePacket.model_validate(node['evidence_packet'])
    claim = next(c for c in packet.claims if ':local-execution:' in (c.id or ''))
    assert claim.classification == 'fact' and claim.confidence == 1
    assert claim.evidence_refs == [node['id']]
    assert claim.id in known_evidence_refs(packet)
    assert 'same object' in claim.text and 'no runtime type restriction' in claim.text
    assert 'body only' in claim.text and 'not itself a side effect' in claim.text


@pytest.mark.parametrize('source', [
    'async def f(value): return value',
    'def f(value): return value.copy()',
    'def f(value): return other',
    'def f(value):\n    value = 3\n    return value',
    'def f(value):\n    yield value\n    return value',
    'def f(value):\n    def inner(): return value',
])
def test_identity_rejects_unsupported_bodies(source):
    assert identity_return_facts(ast.parse(source).body[0]) == []


def test_class_declaration_preserves_parameterized_syntax_without_runtime_claims(tmp_path):
    (tmp_path / 'example.py').write_text('class Empty(Repository[Order]):\n    "doc"\n    pass\nclass Active:\n    def run(self): return self\n')
    nodes = analyze_repository(tmp_path)['nodes']
    for name in ('Empty', 'Active'):
        node = next(n for n in nodes if n.get('name') == name)
        packet = EvidencePacket.model_validate(node['evidence_packet'])
        claim = next(c for c in packet.claims if (c.id or '').endswith(':class-declaration'))
        assert claim.evidence_refs == [node['id']]
        assert claim.id in known_evidence_refs(packet)
        assert 'not a function return or an instance creation' in claim.text
        assert ('only pass statements' in claim.text) == (name == 'Empty')
        if name == 'Empty':
            assert 'Repository[Order]' in claim.text
        assert 'local_execution_facts' not in node
