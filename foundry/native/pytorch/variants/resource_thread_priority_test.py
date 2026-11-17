"""Exercise the actual patched JSON function and pftrace generator AST, no GPU/imports."""
import ast,sys,unittest
from pathlib import Path
source=Path(sys.argv.pop(1)).read_text();tree=ast.parse(source)
fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_runtime_thread_id')
gen=next(n for n in ast.walk(tree) if isinstance(n,ast.GeneratorExp) and 'cpu_thread_by_correlation_id' in ast.unparse(n) and 'thread_resource_map' in ast.unparse(n))
class Col(list):
 def tolist(self):return list(self)
class PriorityTest(unittest.TestCase):
 def test_json_and_pftrace_same_cases(self):
  cases=[('conflict',{7:{-7:123}},{5:(7,99)},123),('mapped_zero',{7:{-7:0}},{5:(7,99)},0),('owner_only',{}, {5:(7,99)},99),('null_mapping',{7:{-7:None}},{5:(7,99)},99),('wrong_owner_pid',{}, {5:(8,99)},-7),('other_process_map',{8:{-7:123}},{},-7),('raw',{}, {},-7)]
  for name,resources,owners,expected in cases:
   with self.subTest(name=name):
    env={'thread_resource_map':resources,'cpu_thread_by_correlation_id':owners};exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'actual-json-function','exec'),env)
    self.assertEqual(env['_runtime_thread_id'](7,5,-7),expected)
    env.update(pid=[7],corr=Col([5]),normtid=Col([-7]));result=list(eval(compile(ast.Expression(gen),'actual-pftrace-generator','eval'),env));self.assertEqual(result,[expected])
if __name__=='__main__':unittest.main()
