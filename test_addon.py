"""使用人工物件驗證演算法與生命週期；不是 NVDA 實機語音測試。"""
import builtins
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from enum import Enum, auto

ROOT = Path(__file__).resolve().parent
builtins._ = lambda value: value
def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    sys.modules[name] = result
    return result

class Role(Enum):
    UNKNOWN=auto(); TABLE=auto(); DOCUMENT=auto(); TABLECELL=auto(); TABLEROW=auto(); TABLECOLUMNHEADER=auto(); EDITABLETEXT=auto()
class State(Enum):
    INVISIBLE=auto(); PROTECTED=auto(); CLICKABLE=auto()
class Base:
    def terminate(self): pass
class Object:
    def __init__(self, role=Role.UNKNOWN, name='', children=(), states=()):
        self.role=role; self.name=name; self.value=''; self.states=set(states)
        self.processID=1; self.parent=None; self.treeInterceptor=None
        self.firstChild=None; self.next=None; self.location=(0,0,100,20)
        self.rowSpan=1; self.columnSpan=1
        if children: self.firstChild=children[0]
        for i, child in enumerate(children):
            child.parent=self
            child.next=children[i+1] if i+1<len(children) else None

messages=[]; dialogs=[]; pending=[]
module('addonHandler', initTranslation=lambda: None)
module('api')
module('core', callLater=lambda delay, callback, *args: pending.append((callback,args)))
module('globalPluginHandler', GlobalPlugin=Base)
module('globalVars', appArgs=types.SimpleNamespace(secure=False))
module('ui', message=messages.append, browseableMessage=lambda *args, **kwargs: dialogs.append((args,kwargs)))
module('controlTypes', Role=Role, State=State)
module('scriptHandler', script=lambda **kwargs: lambda fn: fn)
module('utils'); module('utils.security', isRunningOnSecureDesktop=lambda: False)
module('winAPI'); module('winAPI.sessionTracking', isLockScreenModeActive=lambda: False)
module('logHandler', log=types.SimpleNamespace(debugWarning=lambda msg: None))
spec=importlib.util.spec_from_file_location('tableReader',ROOT/'addon/globalPlugins/tableReader/__init__.py',submodule_search_locations=[str(ROOT/'addon/globalPlugins/tableReader')])
addon=importlib.util.module_from_spec(spec);sys.modules['tableReader']=addon;spec.loader.exec_module(addon)
from tableReader.model import Node, findTables, render

def cell(text, role='TABLECELL', x=0, y=0): return Node(role=role,text=text,rect=(x,y,100,20))
def table():
    return Node(role='TABLE',children=[Node(role='TABLEROW',children=[cell('名稱','TABLECOLUMNHEADER'),cell('數值','TABLECOLUMNHEADER')]),Node(role='TABLEROW',children=[cell('<script>bad</script>'),cell('000123-004567')])])
def live():
    return Object(Role.DOCUMENT,children=[Object(Role.TABLE,children=[Object(Role.TABLEROW,children=[Object(Role.TABLECELL,'A'),Object(Role.TABLECELL,'B')])])])

class Tests(unittest.TestCase):
    def setUp(self):
        messages.clear();dialogs.clear();pending.clear()
        sys.modules['globalVars'].appArgs.secure=False
        self.obj=live()
        sys.modules['api'].getNavigatorObject=lambda:self.obj
        sys.modules['api'].getFocusObject=lambda:self.obj
    def pump(self):
        for unused in range(100):
            if not pending:return
            cb,args=pending.pop(0);cb(*args)
        self.fail('callback loop')
    def test_semantic_exact_and_escape(self):
        result=findTables(table());self.assertEqual(len(result),1)
        self.assertTrue(result[0].header)
        page=render(result);self.assertIn('000123-004567',page);self.assertNotIn('<script>',page);self.assertIn('&lt;script&gt;',page)
    def test_no_header_invention(self):
        source=table();source.children[0].children[0].role='TABLECELL'
        page=render(findTables(source));self.assertIn('第 1 欄',page);self.assertIn('<td>名稱</td>',page)
    def test_merged_rejected(self):
        source=table();source.children[1].children[0].columnSpan=2
        self.assertEqual(findTables(source),[])
    def test_uneven_rejected(self):
        source=table();source.children[1].children.pop();self.assertEqual(findTables(source),[])
    def test_blocked_rejected(self):
        source=table();source.children[1].children[0].blocked=True;self.assertEqual(findTables(source),[])
    def test_inferred_geometry(self):
        source=Node(children=[Node(children=[cell('A','UNKNOWN',0,0),cell('B','UNKNOWN',100,0)]),Node(children=[cell('C','UNKNOWN',0,30),cell('D','UNKNOWN',100,30)])])
        result=findTables(source);self.assertEqual(len(result),1);self.assertTrue(result[0].inferred)
        source.children[1].children[1].rect=(150,30,100,20);self.assertEqual(findTables(source),[])
    def test_plain_text_not_split(self): self.assertEqual(findTables(Node(text='ABC1234562026/11/7')),[])
    def test_wrapped_text_and_unlabelled_action(self):
        source=Node(children=[
            Node(children=[cell('名稱','TABLECOLUMNHEADER',10,0),cell('多行內容','TABLECOLUMNHEADER',110,0)]),
            Node(children=[cell('A','UNKNOWN',0,30),cell('一行\n二行','UNKNOWN',100,30),cell('其他選項','BUTTON',200,30)]),
            Node(children=[cell('B','UNKNOWN',0,60),cell('三行\n四行','UNKNOWN',100,60),cell('其他選項','MENUBUTTON',200,60)])])
        # 置中的標題文字比欄位容器窄，不與右側操作欄重疊。
        for heading in source.children[0].children:
            x,y,w,h=heading.rect;heading.rect=(x,y,80,h)
        result=findTables(source)
        self.assertEqual(len(result),1);self.assertTrue(result[0].header)
        self.assertEqual(result[0].rows[1][1],'一行\n二行')
        self.assertEqual(result[0].rows[0][-1],'操作（補入的欄名）')
        source.children[2].children[-1].role='UNKNOWN'
        self.assertEqual(findTables(source),[])
    def test_duplicate_avoided(self):
        self.assertEqual(len(findTables(Node(children=[table()]))),1)
    def test_scan_and_dialog(self):
        plugin=addon.GlobalPlugin();plugin.start(False);self.pump();self.assertEqual(len(dialogs),1);self.assertIsNone(plugin.scanTask)
    def test_cancel_and_stale_callback(self):
        plugin=addon.GlobalPlugin();plugin.start(False);plugin.start(False);self.pump();self.assertFalse(dialogs);self.assertIsNone(plugin.scanTask)
    def test_terminate(self):
        plugin=addon.GlobalPlugin();plugin.start(False);plugin.terminate();self.pump();self.assertFalse(dialogs)
    def test_secure(self):
        sys.modules['globalVars'].appArgs.secure=True
        plugin=addon.GlobalPlugin();plugin.start(False);self.assertFalse(pending)
    def test_focus_change(self):
        plugin=addon.GlobalPlugin();plugin.start(False)
        other=Object();other.processID=2;sys.modules['api'].getFocusObject=lambda:other
        self.pump();self.assertIsNone(plugin.scanTask);self.assertFalse(dialogs)
    def test_cycle_and_limits(self):
        node=Object(Role.DOCUMENT,children=[Object(name='A')]);node.firstChild.next=node.firstChild
        with self.assertRaises(addon.ScanLimit):list(addon.capture(node,[0,0]))
        with self.assertRaises(addon.ScanLimit):list(addon.capture(node,[1800,0]))
    def test_password_not_read(self):
        node=Object(states=[State.PROTECTED],name='secret')
        iterator=addon.capture(node,[0,0]);next(iterator)
        with self.assertRaises(StopIteration) as result:next(iterator)
        self.assertEqual(result.exception.value.text,'');self.assertTrue(result.exception.value.blocked)
    def test_timeout(self):
        plugin=addon.GlobalPlugin();plugin.start(False);plugin.started-=9;self.pump();self.assertFalse(dialogs);self.assertIsNone(plugin.scanTask)

if __name__=='__main__':unittest.main()
