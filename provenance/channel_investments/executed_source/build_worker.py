"""Adapt the accepted driver by adding isolated paths and observational hooks."""
from pathlib import Path
import ast
from common import R, CASES


def expression(text):
    return ast.parse(text, mode='eval').body


def build():
    source = (R / 'sources/full_worker.py').read_text(encoding='utf8')
    source = source.replace('E:/Roko/remote_access/final_frozen_profiles_20261031/multisector', R.as_posix())
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and any(isinstance(e, ast.Constant) and e.value == 'heat_input' for e in node.elts):
            node.elts.extend([ast.Constant('h2_sto_soc'), ast.Constant('h2_sto_out'), ast.Constant('extra_cap_inv')])
    replaced = set()
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.Assert) and ast.unparse(node.test) == "case in ('avg', 'low', 'high')":
            node.test = expression('case in ' + repr(CASES))
        if isinstance(node, ast.Assign):
            names = [n.id for n in node.targets if isinstance(n, ast.Name)]
            if names == ['MAPPING']:
                node.value = expression("ROOT / 'baseline/legacy_whole_path_03/mapping_20261030'")
            elif names == ['PROJECT']:
                node.value = expression("ROOT / 'projects' / case")
        if isinstance(node, ast.FunctionDef) and node.name == '_control_before':
            tree.body[i] = ast.parse('def _control_before(model,ctx):\n channel_before(model,ctx,globals())').body[0]
            replaced.add('before')
        if isinstance(node, ast.If) and ast.unparse(node.test) == 'matched':
            node.body = ast.parse("""
assert reference_mode and case == 'average' and matched in YEARS
accepted=[]
start=YEARS.index(matched)
snapshot=dataread(OLD/'multisector/jobs/avg_path'/str(matched)/f'inherited_{matched}.pkl')
""").body
        if isinstance(node, ast.Expr) and ast.unparse(node).startswith('exec(compile(module,'):
            tree.body[i:i+1] = [node] + ast.parse("""
from common import save as common_save, replace as safe_replace, OLD
from hooks import before as channel_before, prebuild as channel_prebuild, terms as channel_terms, ReferenceDone
def save(name,v):
 common_save(OUT/name,v)
reference_mode = bool(matched)
def _channel_prebuild(ctx):
 channel_prebuild(ctx,globals())
""").body
            replaced.add('imports')
            break
    # The inserted definitions shift top-level indices; visit functions in a second pass.
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.FunctionDef) and node.name == '_control_before':
            tree.body[i] = ast.parse('def _control_before(model,ctx):\n channel_before(model,ctx,globals())').body[0]
            replaced.add('before')
        if isinstance(node, ast.If) and ast.unparse(node.test) == 'matched':
            node.body = ast.parse("""
assert reference_mode and case == 'average' and matched in YEARS
accepted=[]
start=YEARS.index(matched)
snapshot=dataread(OLD/'multisector/jobs/avg_path'/str(matched)/f'inherited_{matched}.pkl')
""").body
        if isinstance(node, ast.FunctionDef) and node.name == '_full_checkpoint':
            for j, item in enumerate(node.body):
                if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'expected' for t in item.targets):
                    item.value.elts.extend([expression("f'inherited_{year}.pkl'"),
                                           ast.Constant('prebuild_validation.json'), ast.Constant('investment_terms.csv')])
                    node.body.insert(j, ast.parse('channel_terms(ctx, model, OUT)').body[0])
                    replaced.add('terms')
                    break
        if isinstance(node, ast.Try):
            handler = ast.parse("""
try:
 pass
except ReferenceDone:
 OUT=JOB
 save('reference_completed.json', {'status':'reference_checked','year':matched,'fence':fence})
""").body[0].handlers[0]
            node.handlers.insert(0, handler)
            replaced.add('reference_handler')

    class Edits(ast.NodeTransformer):
        def visit_Constant(self, node):
            if node.value == 'temperature=avg_yield__normal':
                return ast.copy_location(ast.Constant(''), node)
            return node

        def visit_BinOp(self, node):
            if ast.unparse(node) == "case + '_yield__normal'":
                return expression("'avg_yield__normal' if case == 'average' else case + '__channel'")
            return self.generic_visit(node)

        def visit_Call(self, node):
            if ast.unparse(node.func) == 'temp.replace':
                return ast.copy_location(ast.Call(func=ast.Name(id='safe_replace', ctx=ast.Load()),
                                                 args=[ast.Name(id='temp', ctx=ast.Load())] + node.args, keywords=[]), node)
            return self.generic_visit(node)

        def visit_Dict(self, node):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == '_full_checkpoint':
                    node.keys.append(ast.Constant('_channel_prebuild'))
                    node.values.append(ast.Name(id='_channel_prebuild', ctx=ast.Load()))
                    break
            return self.generic_visit(node)

    tree = ast.fix_missing_locations(Edits().visit(tree))
    assert replaced == {'before', 'imports', 'terms', 'reference_handler'}, replaced
    code = ast.unparse(tree) + '\n'
    compile(code, 'full_worker.py', 'exec')
    (R / 'full_worker.py').write_text(code, encoding='utf8')


if __name__ == '__main__':
    build()
